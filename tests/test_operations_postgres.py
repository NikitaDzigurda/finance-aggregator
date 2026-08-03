import os
from collections.abc import AsyncIterator
from typing import Any
from uuid import uuid4

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import delete
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from apps.api.main import create_app
from operations.models import OperationModel
from shared.database import get_db_session


@pytest.mark.postgres
@pytest.mark.asyncio
async def test_manual_ledger_operations_round_trip_and_filter() -> None:
    database_url = os.getenv("FINANCE_TEST_DATABASE_URL")
    if database_url is None:
        pytest.skip("FINANCE_TEST_DATABASE_URL is not configured")

    engine = create_async_engine(database_url)
    sessions = async_sessionmaker(engine, expire_on_commit=False)

    async def override_session() -> AsyncIterator[AsyncSession]:
        async with sessions() as session:
            try:
                yield session
            except Exception:
                await session.rollback()
                raise

    application = create_app()
    application.dependency_overrides[get_db_session] = override_session
    transport = ASGITransport(app=application)
    suffix = uuid4().hex[:8].upper()
    portfolio_id: str | None = None
    instrument_id: str | None = None

    try:
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            portfolio_response = await client.post(
                "/api/v1/portfolios",
                json={"name": f"Ledger {suffix}", "base_currency": "USD"},
            )
            portfolio_id = portfolio_response.json()["id"]
            account_response = await client.post(
                f"/api/v1/portfolios/{portfolio_id}/accounts",
                json={"name": "Synthetic broker", "account_type": "broker"},
            )
            account_id = account_response.json()["id"]
            instrument_response = await client.post(
                "/api/v1/instruments",
                json={
                    "name": f"Ledger Instrument {suffix}",
                    "instrument_type": "stock",
                    "currency": "USD",
                    "identifiers": [
                        {
                            "identifier_type": "ticker",
                            "value": f"L{suffix[:6]}",
                            "exchange": "XNAS",
                        }
                    ],
                },
            )
            instrument_id = instrument_response.json()["id"]

            common = {
                "portfolio_id": portfolio_id,
                "account_id": account_id,
                "occurred_at": "2026-08-03T10:30:00Z",
                "time_precision": "second",
            }
            mismatched_account_response = await client.post(
                "/api/v1/operations",
                json={
                    **common,
                    "portfolio_id": str(uuid4()),
                    "operation_type": "fee",
                    "payload": {"amount": "1", "currency": "USD"},
                },
            )
            assert mismatched_account_response.status_code == 404

            operations: list[dict[str, Any]] = [
                {
                    **common,
                    "operation_type": "trade",
                    "payload": {
                        "side": "buy",
                        "instrument_id": instrument_id,
                        "quantity": "10.000000000000000001",
                        "price": "125.125",
                        "price_currency": "USD",
                    },
                },
                {
                    **common,
                    "operation_type": "trade",
                    "payload": {
                        "side": "sell",
                        "instrument_id": instrument_id,
                        "quantity": "2.5",
                        "price": "130.75",
                        "price_currency": "USD",
                    },
                },
                {
                    **common,
                    "operation_type": "fee",
                    "payload": {
                        "amount": "1.250000000000000001",
                        "currency": "USD",
                        "instrument_id": instrument_id,
                    },
                },
                {
                    **common,
                    "operation_type": "tax",
                    "payload": {
                        "amount": "0.75",
                        "currency": "USD",
                        "instrument_id": instrument_id,
                    },
                },
                {
                    **common,
                    "operation_type": "income",
                    "payload": {
                        "income_type": "dividend",
                        "amount": "12.345678901234567890",
                        "currency": "USD",
                        "instrument_id": instrument_id,
                    },
                },
                {
                    **common,
                    "operation_type": "cash_movement",
                    "payload": {
                        "direction": "deposit",
                        "amount": "1000.01",
                        "currency": "USD",
                    },
                },
            ]

            for payload in operations:
                response = await client.post("/api/v1/operations", json=payload)
                assert response.status_code == 201, response.text
                body = response.json()
                assert body["payload"] == payload["payload"]
                assert body["source"] == {
                    "type": "manual",
                    "import_batch_id": None,
                    "source_operation_id": None,
                    "row_number": None,
                }

            account_filter = await client.get(
                "/api/v1/operations",
                params={"account_id": account_id},
            )
            assert account_filter.status_code == 200
            assert len(account_filter.json()["items"]) == 6

            trade_filter = await client.get(
                "/api/v1/operations",
                params={"operation_type": "trade", "instrument_id": instrument_id},
            )
            assert trade_filter.status_code == 200
            assert len(trade_filter.json()["items"]) == 2
            assert {item["payload"]["side"] for item in trade_filter.json()["items"]} == {
                "buy",
                "sell",
            }

            period_filter = await client.get(
                "/api/v1/operations",
                params={"occurred_from": "2026-08-03T10:31:00Z"},
            )
            assert period_filter.status_code == 200
            assert period_filter.json()["items"] == []
    finally:
        if portfolio_id is not None:
            async with sessions.begin() as session:
                await session.execute(
                    delete(OperationModel).where(OperationModel.portfolio_id == portfolio_id)
                )
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            if instrument_id is not None:
                await client.delete(f"/api/v1/instruments/{instrument_id}")
            if portfolio_id is not None:
                await client.delete(f"/api/v1/portfolios/{portfolio_id}")
        application.dependency_overrides.clear()
        await engine.dispose()
