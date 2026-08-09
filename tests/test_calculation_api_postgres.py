import os
from collections.abc import AsyncIterator
from uuid import UUID, uuid4

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import delete
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from apps.api.main import create_app
from instruments.models import InstrumentModel
from operations.models import OperationModel
from portfolios.models import PortfolioModel
from pricing.models import MarketPriceModel
from shared.database import get_db_session


@pytest.mark.postgres
@pytest.mark.asyncio
async def test_position_recalculation_api_is_exact_repeatable_and_diagnostic() -> None:
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
    portfolio_id: UUID | None = None
    instrument_ids: list[UUID] = []

    try:
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            portfolio = await client.post(
                "/api/v1/portfolios",
                json={"name": f"Calculation {suffix}", "base_currency": "USD"},
            )
            assert portfolio.status_code == 201, portfolio.text
            portfolio_id = UUID(portfolio.json()["id"])
            account = await client.post(
                f"/api/v1/portfolios/{portfolio_id}/accounts",
                json={"name": "Synthetic CEX", "account_type": "cex"},
            )
            assert account.status_code == 201, account.text
            account_id = account.json()["id"]

            for name, code in (("Synthetic BTC", "BTC"), ("Synthetic Missing", "MIS")):
                instrument = await client.post(
                    "/api/v1/instruments",
                    json={
                        "name": f"{name} {suffix}",
                        "instrument_type": "crypto_asset",
                        "currency": "USD",
                        "identifiers": [
                            {
                                "identifier_type": "crypto_asset_code",
                                "value": f"{code}{suffix}",
                            }
                        ],
                    },
                )
                assert instrument.status_code == 201, instrument.text
                instrument_ids.append(UUID(instrument.json()["id"]))

            common = {
                "portfolio_id": str(portfolio_id),
                "account_id": account_id,
                "time_precision": "second",
            }
            operations = [
                {
                    **common,
                    "occurred_at": "2026-08-01T10:00:00Z",
                    "operation_type": "cash_movement",
                    "payload": {
                        "direction": "deposit",
                        "amount": "100000",
                        "currency": "USD",
                    },
                },
                {
                    **common,
                    "occurred_at": "2026-08-02T10:00:00Z",
                    "operation_type": "trade",
                    "payload": {
                        "side": "buy",
                        "instrument_id": str(instrument_ids[0]),
                        "quantity": "10",
                        "price": "100",
                        "price_currency": "USD",
                    },
                },
                {
                    **common,
                    "occurred_at": "2026-08-03T10:00:00Z",
                    "operation_type": "trade",
                    "payload": {
                        "side": "buy",
                        "instrument_id": str(instrument_ids[0]),
                        "quantity": "10",
                        "price": "200",
                        "price_currency": "USD",
                    },
                },
                {
                    **common,
                    "occurred_at": "2026-08-04T10:00:00Z",
                    "operation_type": "trade",
                    "payload": {
                        "side": "sell",
                        "instrument_id": str(instrument_ids[0]),
                        "quantity": "5",
                        "price": "180",
                        "price_currency": "USD",
                    },
                },
                {
                    **common,
                    "occurred_at": "2026-08-05T10:00:00Z",
                    "operation_type": "fee",
                    "payload": {"amount": "7.5", "currency": "EUR"},
                },
                {
                    **common,
                    "occurred_at": "2026-08-06T10:00:00Z",
                    "operation_type": "trade",
                    "payload": {
                        "side": "sell",
                        "instrument_id": str(instrument_ids[1]),
                        "quantity": "1",
                        "price": "50",
                        "price_currency": "USD",
                    },
                },
            ]
            for payload in operations:
                response = await client.post("/api/v1/operations", json=payload)
                assert response.status_code == 201, response.text

            price = await client.post(
                "/api/v1/prices",
                json={
                    "instrument_id": str(instrument_ids[0]),
                    "price": "210",
                    "currency": "USD",
                    "observed_at": "2026-08-07T10:00:00Z",
                },
            )
            assert price.status_code == 201, price.text
            assert price.json()["price"] == "210.000000000000000000"

            request = {
                "as_of": "2026-08-08T10:00:00Z",
                "cost_basis_method": "weighted_average",
            }
            first = await client.post(
                f"/api/v1/portfolios/{portfolio_id}/positions/recalculate",
                json=request,
            )
            assert first.status_code == 200, first.text
            first_body = first.json()
            assert first_body["status"] == "completed_with_diagnostics"
            assert first_body["operation_count"] == 6
            positions = {
                item["instrument_id"]: item for item in first_body["positions"]
            }
            main_position = positions[str(instrument_ids[0])]
            assert main_position["quantity"] == "15.000000000000000000"
            assert main_position["average_cost"] == "150.000000000000000000"
            assert main_position["realised_pnl"] == "150.000000000000000000"
            assert main_position["unrealised_pnl"] == "900.000000000000000000"
            assert positions[str(instrument_ids[1])]["quantity"] == (
                "-1.000000000000000000"
            )
            assert {(item["severity"], item["code"]) for item in first_body["diagnostics"]} == {
                ("error", "negative_position"),
                ("warning", "market_price_missing"),
            }
            balances = {
                item["currency"]: item["amount"]
                for item in first_body["cash_balances"]
            }
            assert balances == {
                "EUR": "-7.500000000000000000",
                "USD": "97950.000000000000000000",
            }

            second = await client.post(
                f"/api/v1/portfolios/{portfolio_id}/positions/recalculate",
                json=request,
            )
            assert second.status_code == 200, second.text
            second_body = second.json()
            for key in (
                "status",
                "as_of",
                "operation_count",
                "diagnostics",
                "positions",
                "cash_balances",
                "currency_metrics",
            ):
                assert second_body[key] == first_body[key]

            stored = await client.get(f"/api/v1/portfolios/{portfolio_id}/positions")
            assert stored.status_code == 200
            assert stored.json() == second_body
    finally:
        async with sessions.begin() as session:
            if instrument_ids:
                await session.execute(
                    delete(MarketPriceModel).where(
                        MarketPriceModel.instrument_id.in_(instrument_ids)
                    )
                )
            if portfolio_id is not None:
                await session.execute(
                    delete(OperationModel).where(
                        OperationModel.portfolio_id == portfolio_id
                    )
                )
                await session.execute(
                    delete(PortfolioModel).where(PortfolioModel.id == portfolio_id)
                )
            if instrument_ids:
                await session.execute(
                    delete(InstrumentModel).where(InstrumentModel.id.in_(instrument_ids))
                )
        application.dependency_overrides.clear()
        await engine.dispose()
