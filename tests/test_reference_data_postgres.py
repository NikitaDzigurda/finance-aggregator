import os
from collections.abc import AsyncIterator
from uuid import uuid4

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from apps.api.main import create_app
from shared.database import get_db_session


@pytest.mark.postgres
@pytest.mark.asyncio
async def test_portfolio_accounts_and_instrument_api_happy_path() -> None:
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
                json={"name": f"Synthetic {suffix}", "base_currency": "USD"},
            )
            assert portfolio_response.status_code == 201, portfolio_response.text
            portfolio = portfolio_response.json()
            portfolio_id = portfolio["id"]
            assert portfolio["base_currency"] == "USD"

            for account_type in ("broker", "bank", "cex"):
                account_response = await client.post(
                    f"/api/v1/portfolios/{portfolio_id}/accounts",
                    json={
                        "name": f"Synthetic {account_type}",
                        "account_type": account_type,
                        "institution_name": "Synthetic Institution",
                    },
                )
                assert account_response.status_code == 201, account_response.text

            accounts_response = await client.get(
                f"/api/v1/portfolios/{portfolio_id}/accounts"
            )
            assert accounts_response.status_code == 200
            assert {item["account_type"] for item in accounts_response.json()["items"]} == {
                "broker",
                "bank",
                "cex",
            }

            instrument_payload = {
                "name": f"Synthetic Equity {suffix}",
                "instrument_type": "stock",
                "currency": "USD",
                "identifiers": [
                    {
                        "identifier_type": "ticker",
                        "value": f"S{suffix[:6]}",
                        "exchange": "XNAS",
                    },
                    {"identifier_type": "provider_code", "value": suffix, "provider": "synthetic"},
                ],
            }
            instrument_response = await client.post(
                "/api/v1/instruments",
                json=instrument_payload,
            )
            assert instrument_response.status_code == 201, instrument_response.text
            instrument = instrument_response.json()
            instrument_id = instrument["id"]
            assert len(instrument["identifiers"]) == 2

            duplicate_response = await client.post(
                "/api/v1/instruments",
                json={**instrument_payload, "name": f"Duplicate {suffix}"},
            )
            assert duplicate_response.status_code == 409
            assert duplicate_response.json()["error"]["code"] == (
                "instrument_identifier_conflict"
            )

            update_response = await client.patch(
                f"/api/v1/portfolios/{portfolio_id}",
                json={"base_currency": "EUR"},
            )
            assert update_response.status_code == 200
            assert update_response.json()["base_currency"] == "EUR"
    finally:
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            if instrument_id is not None:
                await client.delete(f"/api/v1/instruments/{instrument_id}")
            if portfolio_id is not None:
                await client.delete(f"/api/v1/portfolios/{portfolio_id}")
        application.dependency_overrides.clear()
        await engine.dispose()
