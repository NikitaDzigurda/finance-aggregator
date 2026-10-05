"""High-risk price ordering and saved-valuation consistency, using synthetic data only."""

import os
from collections.abc import AsyncIterator
from datetime import UTC, datetime
from decimal import Decimal
from uuid import UUID, uuid4

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import delete
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from apps.api.main import create_app
from calculation.models import CalculationSnapshotModel
from instruments.models import InstrumentModel
from operations.models import OperationModel
from portfolios.models import PortfolioModel
from pricing.models import MarketMappingModel, MarketPriceModel
from shared.database import get_db_session


@pytest.mark.postgres
@pytest.mark.asyncio
async def test_manual_tie_historical_limit_and_saved_price_provenance() -> None:
    database_url = os.getenv("FINANCE_TEST_DATABASE_URL")
    if database_url is None:
        pytest.skip("FINANCE_TEST_DATABASE_URL is not configured")
    engine = create_async_engine(database_url)
    sessions = async_sessionmaker(engine, expire_on_commit=False)

    async def override_session() -> AsyncIterator[AsyncSession]:
        async with sessions() as session:
            yield session

    app = create_app()
    app.dependency_overrides[get_db_session] = override_session
    portfolio_id: UUID | None = None
    instrument_id: UUID | None = None
    mapping_id: UUID | None = None
    try:
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            portfolio = await client.post(
                "/api/v1/portfolios",
                json={
                    "name": f"Synthetic price selection {uuid4().hex[:8]}",
                    "base_currency": "USD",
                },
            )
            assert portfolio.status_code == 201, portfolio.text
            portfolio_id = UUID(portfolio.json()["id"])
            account = await client.post(
                f"/api/v1/portfolios/{portfolio_id}/accounts",
                json={"name": "Synthetic broker", "account_type": "broker"},
            )
            assert account.status_code == 201, account.text
            isin = f"XS{uuid4().hex[:9].upper()}0"
            symbol = f"SYN{uuid4().hex[:8].upper()}"
            instrument = await client.post(
                "/api/v1/instruments",
                json={
                    "name": f"Synthetic share {uuid4().hex[:8]}",
                    "instrument_type": "stock",
                    "currency": "USD",
                    "identifiers": [
                        {
                            "identifier_type": "isin",
                            "value": isin,
                        }
                    ],
                },
            )
            assert instrument.status_code == 201, instrument.text
            instrument_id = UUID(instrument.json()["id"])
            operation = await client.post(
                "/api/v1/operations",
                json={
                    "portfolio_id": str(portfolio_id),
                    "account_id": account.json()["id"],
                    "occurred_at": "2026-09-01T09:00:00Z",
                    "time_precision": "second",
                    "operation_type": "trade",
                    "payload": {
                        "side": "buy",
                        "instrument_id": str(instrument_id),
                        "quantity": "2",
                        "price": "10",
                        "price_currency": "USD",
                    },
                },
            )
            assert operation.status_code == 201, operation.text
            manual = await client.post(
                "/api/v1/prices",
                json={
                    "instrument_id": str(instrument_id),
                    "price": "12.125",
                    "currency": "USD",
                    "observed_at": "2026-09-02T12:00:00Z",
                },
            )
            assert manual.status_code == 201, manual.text
            manual_id = manual.json()["id"]

            async with sessions.begin() as session:
                mapping = MarketMappingModel(
                    instrument_id=instrument_id,
                    provider="synthetic_test_only",
                    market="SIM",
                    symbol=symbol,
                    quote_currency="USD",
                    identifier_type="isin",
                    identifier_value=isin,
                    price_unit="security_unit",
                    price_kind="last_trade",
                    time_quality="provider_trade",
                    status="verified",
                    confirmation_source="synthetic_test_fixture",
                    confirmed_at=datetime(2026, 9, 2, tzinfo=UTC),
                )
                session.add(mapping)
                await session.flush()
                mapping_id = mapping.id

            async with sessions.begin() as session:
                session.add(
                    MarketPriceModel(
                        instrument_id=instrument_id,
                        mapping_id=mapping_id,
                        price=Decimal("999"),
                        currency="USD",
                        observed_at=datetime(2026, 9, 2, 12, tzinfo=UTC),
                        fetched_at=datetime(2026, 9, 2, 12, 1, tzinfo=UTC),
                        source="automatic",
                        provider="synthetic_test_only",
                        price_kind="last_trade",
                        time_quality="provider_trade",
                    )
                )
            resolved = await client.get(
                "/api/v1/prices/resolve",
                params={
                    "instrument_id": str(instrument_id),
                    "valuation_as_of": "2026-09-02T12:30:00Z",
                },
            )
            assert resolved.status_code == 200, resolved.text
            assert resolved.json()["observation"]["id"] == manual_id

            recalculated = await client.post(
                f"/api/v1/portfolios/{portfolio_id}/positions/recalculate",
                json={"as_of": "2026-09-02T13:00:00Z"},
            )
            assert recalculated.status_code == 200, recalculated.text
            assert recalculated.json()["positions"][0]["market_value"] == ("24.250000000000000000")
            holdings = await client.get(
                f"/api/v1/portfolios/{portfolio_id}/analytics/holdings",
                params={"reporting_currency": "USD"},
            )
            assert holdings.status_code == 200, holdings.text
            item = next(
                item
                for item in holdings.json()["items"]
                if item["instrument_id"] == str(instrument_id)
            )
            assert item["price_observation"]["id"] == manual_id
            assert item["market_value"] == "24.250000000000000000"

            async with sessions.begin() as session:
                session.add(
                    MarketPriceModel(
                        instrument_id=instrument_id,
                        mapping_id=mapping_id,
                        price=Decimal("13.375"),
                        currency="USD",
                        observed_at=datetime(2026, 9, 3, 12, tzinfo=UTC),
                        fetched_at=datetime(2026, 9, 3, 12, 1, tzinfo=UTC),
                        source="automatic",
                        provider="synthetic_test_only",
                        price_kind="last_trade",
                        time_quality="provider_trade",
                    )
                )
            old = await client.get(f"/api/v1/portfolios/{portfolio_id}/analytics/holdings")
            assert old.status_code == 200, old.text
            assert old.json()["snapshot_fresh"] is False
            old_item = next(
                item for item in old.json()["items"] if item["instrument_id"] == str(instrument_id)
            )
            assert old_item["price_observation"]["id"] == manual_id
            assert old_item["market_value"] == "24.250000000000000000"
            historical = await client.get(
                "/api/v1/prices/resolve",
                params={
                    "instrument_id": str(instrument_id),
                    "valuation_as_of": "2026-09-02T13:00:00Z",
                },
            )
            assert historical.json()["observation"]["id"] == manual_id
            updated = await client.post(
                f"/api/v1/portfolios/{portfolio_id}/positions/recalculate",
                json={"as_of": "2026-09-04T00:00:00Z"},
            )
            assert updated.status_code == 200, updated.text
            assert updated.json()["positions"][0]["market_value"] == ("26.750000000000000000")
            fresh = await client.get(f"/api/v1/portfolios/{portfolio_id}/analytics/holdings")
            assert fresh.status_code == 200, fresh.text
            assert fresh.json()["snapshot_fresh"] is True
            fresh_item = next(
                item
                for item in fresh.json()["items"]
                if item["instrument_id"] == str(instrument_id)
            )
            assert fresh_item["price_observation"]["source"] == "automatic"
            assert fresh_item["price_observation"]["price"] == ("13.375000000000000000")
    finally:
        async with sessions.begin() as session:
            if portfolio_id is not None:
                await session.execute(
                    delete(CalculationSnapshotModel).where(
                        CalculationSnapshotModel.portfolio_id == portfolio_id
                    )
                )
            if instrument_id is not None:
                await session.execute(
                    delete(MarketPriceModel).where(MarketPriceModel.instrument_id == instrument_id)
                )
                await session.execute(
                    delete(MarketMappingModel).where(
                        MarketMappingModel.instrument_id == instrument_id
                    )
                )
            if portfolio_id is not None:
                await session.execute(
                    delete(OperationModel).where(OperationModel.portfolio_id == portfolio_id)
                )
                await session.execute(
                    delete(PortfolioModel).where(PortfolioModel.id == portfolio_id)
                )
            if instrument_id is not None:
                await session.execute(
                    delete(InstrumentModel).where(InstrumentModel.id == instrument_id)
                )
        app.dependency_overrides.clear()
        await engine.dispose()
