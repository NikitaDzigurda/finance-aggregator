"""High-risk offline market ingestion with synthetic instruments and prices only."""

import os
from collections.abc import AsyncIterator
from datetime import UTC, datetime
from decimal import Decimal
from uuid import UUID, uuid4

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

import pricing.market_data as market_data_module
from apps.api.main import create_app
from calculation.models import CalculationSnapshotModel
from imports.models import ImportJobModel
from instruments.models import InstrumentModel
from operations.models import OperationModel
from portfolios.models import PortfolioModel
from pricing.market_data import (
    MarketDataConflict,
    MarketQuote,
    MarketSnapshot,
    enqueue_market_data_sync,
    synchronize_market_snapshot,
)
from pricing.models import MarketMappingModel, MarketPriceModel, MarketPriceUnit
from shared.database import get_db_session


class _SyntheticSnapshotProvider:
    provider_id = "synthetic_offline_test"
    market = "SIM"

    def __init__(self, price: Decimal, *, observed_at: datetime) -> None:
        self.price = price
        self.observed_at = observed_at

    async def fetch_snapshot(self) -> MarketSnapshot:
        fetched_at = datetime(2026, 9, 3, 12, 1, tzinfo=UTC)
        return MarketSnapshot(
            provider=self.provider_id,
            market=self.market,
            quotes=(
                MarketQuote(
                    symbol=self.symbol,
                    quote_currency="USD",
                    price_unit=MarketPriceUnit.SECURITY_UNIT,
                    price_kind="last_trade",
                    time_quality="provider_trade",
                    price=self.price,
                    observed_at=self.observed_at,
                    fetched_at=fetched_at,
                ),
                MarketQuote(
                    symbol=self.symbol,
                    quote_currency="EUR",
                    price_unit=MarketPriceUnit.SECURITY_UNIT,
                    price_kind="last_trade",
                    time_quality="provider_trade",
                    price=Decimal("999"),
                    observed_at=self.observed_at,
                    fetched_at=fetched_at,
                ),
                MarketQuote(
                    symbol=self.symbol,
                    quote_currency="USD",
                    price_unit=MarketPriceUnit.CRYPTO_UNIT,
                    price_kind="last_trade",
                    time_quality="provider_trade",
                    price=Decimal("999"),
                    observed_at=self.observed_at,
                    fetched_at=fetched_at,
                ),
                MarketQuote(
                    symbol=self.symbol,
                    quote_currency="USD",
                    price_unit=MarketPriceUnit.SECURITY_UNIT,
                    price_kind="index_value",
                    time_quality="provider_trade",
                    price=Decimal("999"),
                    observed_at=self.observed_at,
                    fetched_at=fetched_at,
                ),
                MarketQuote(
                    symbol=self.symbol,
                    quote_currency="USD",
                    price_unit=MarketPriceUnit.SECURITY_UNIT,
                    price_kind="last_trade",
                    time_quality="provider_trade",
                    price=Decimal("-1"),
                    observed_at=self.observed_at,
                    fetched_at=fetched_at,
                ),
            ),
        )


@pytest.mark.postgres
@pytest.mark.asyncio
async def test_reviewed_mapping_idempotent_snapshot_and_background_recalculation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
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
    job_id: UUID | None = None
    try:
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            suffix = uuid4().hex[:9].upper()
            isin = f"XS{suffix}0"
            symbol = f"SYN{suffix}"
            portfolio = await client.post(
                "/api/v1/portfolios",
                json={"name": f"Synthetic market sync {suffix}", "base_currency": "USD"},
            )
            assert portfolio.status_code == 201, portfolio.text
            portfolio_id = UUID(portfolio.json()["id"])
            account = await client.post(
                f"/api/v1/portfolios/{portfolio_id}/accounts",
                json={"name": "Synthetic broker", "account_type": "broker"},
            )
            assert account.status_code == 201, account.text
            instrument = await client.post(
                "/api/v1/instruments",
                json={
                    "name": f"Synthetic unit {suffix}",
                    "instrument_type": "stock",
                    "currency": "USD",
                    "identifiers": [{"identifier_type": "isin", "value": isin}],
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
                    "price": "12",
                    "currency": "USD",
                    "observed_at": "2026-09-02T12:00:00Z",
                },
            )
            assert manual.status_code == 201, manual.text
            initial = await client.post(
                f"/api/v1/portfolios/{portfolio_id}/positions/recalculate",
                json={"as_of": "2026-09-02T13:00:00Z"},
            )
            assert initial.status_code == 200, initial.text
            assert initial.json()["positions"][0]["market_value"] == "24.000000000000000000"

            empty_mapping = await client.get(f"/api/v1/instruments/{instrument_id}/market-mappings")
            assert empty_mapping.status_code == 200, empty_mapping.text
            assert empty_mapping.json()["status"] == "unmapped"
            async with sessions.begin() as session:
                session.add(
                    MarketMappingModel(
                        instrument_id=instrument_id,
                        provider="synthetic_offline_test",
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
                        confirmed_at=datetime(2026, 9, 3, tzinfo=UTC),
                    )
                )
            mapping_view = await client.get(f"/api/v1/instruments/{instrument_id}/market-mappings")
            assert mapping_view.status_code == 200, mapping_view.text
            assert mapping_view.json()["status"] == "verified"
            assert mapping_view.json()["items"][0]["eligible"] is True

            async with sessions() as session:
                job, duplicate = await enqueue_market_data_sync(
                    session,
                    provider="synthetic_offline_test",
                    market="SIM",
                    minimum_interval_seconds=3600,
                )
                job_id = job.id
                assert duplicate is False
            async with sessions() as session:
                same_job, duplicate = await enqueue_market_data_sync(
                    session,
                    provider="synthetic_offline_test",
                    market="SIM",
                    minimum_interval_seconds=3600,
                )
                assert duplicate is True and same_job.id == job_id

            observed_at = datetime(2026, 9, 3, 12, tzinfo=UTC)
            provider = _SyntheticSnapshotProvider(Decimal("13.125"), observed_at=observed_at)
            provider.symbol = symbol
            first = await synchronize_market_snapshot(sessions, provider)
            assert first.ingest.inserted == 1
            assert first.ingest.skipped_unmapped == 3
            assert first.ingest.skipped_invalid == 1
            assert first.recalculated_portfolios == (portfolio_id,)
            assert not first.failed_portfolios
            saved = await client.get(f"/api/v1/portfolios/{portfolio_id}/positions")
            assert saved.status_code == 200, saved.text
            position = saved.json()["positions"][0]
            assert position["market_value"] == "26.250000000000000000"
            assert position["cost_basis"] == "20.000000000000000000"

            repeated = await synchronize_market_snapshot(sessions, provider)
            assert repeated.ingest.inserted == 0
            assert repeated.ingest.repeated == 1
            async with sessions() as session:
                count = await session.scalar(
                    select(func.count())
                    .select_from(MarketPriceModel)
                    .where(MarketPriceModel.instrument_id == instrument_id)
                )
                assert count == 2  # One manual, one automatic.
            provider.price = Decimal("14")
            with pytest.raises(MarketDataConflict):
                await synchronize_market_snapshot(sessions, provider)
            still_saved = await client.get(f"/api/v1/portfolios/{portfolio_id}/positions")
            assert still_saved.json()["positions"][0]["market_value"] == "26.250000000000000000"

            provider.observed_at = datetime(2026, 9, 3, 12, 0, 30, tzinfo=UTC)
            real_recalculate = market_data_module.recalculate_portfolio

            async def fail_recalculation(*args: object, **kwargs: object) -> None:
                del args, kwargs
                raise RuntimeError("synthetic calculation failure")

            monkeypatch.setattr(market_data_module, "recalculate_portfolio", fail_recalculation)
            failed = await synchronize_market_snapshot(sessions, provider)
            assert failed.ingest.inserted == 1
            assert failed.failed_portfolios == (portfolio_id,)
            after_failure = await client.get(f"/api/v1/portfolios/{portfolio_id}/positions")
            assert after_failure.json()["positions"][0]["market_value"] == ("26.250000000000000000")
            monkeypatch.setattr(market_data_module, "recalculate_portfolio", real_recalculate)
            retried = await synchronize_market_snapshot(sessions, provider)
            assert retried.ingest.inserted == 0 and retried.ingest.repeated == 1
            assert retried.recalculated_portfolios == (portfolio_id,)
            after_retry = await client.get(f"/api/v1/portfolios/{portfolio_id}/positions")
            assert after_retry.json()["positions"][0]["market_value"] == ("28.000000000000000000")

            async with sessions.begin() as session:
                mapping = await session.scalar(
                    select(MarketMappingModel).where(
                        MarketMappingModel.instrument_id == instrument_id
                    )
                )
                assert mapping is not None
                mapping.status = "ambiguous"
                mapping.reason_code = "provider_code_ambiguous"
            revoked = await client.get(
                "/api/v1/prices/resolve",
                params={
                    "instrument_id": str(instrument_id),
                    "valuation_as_of": "2026-09-04T00:00:00Z",
                },
            )
            assert revoked.status_code == 200, revoked.text
            assert revoked.json()["observation"]["source"] == "manual"
    finally:
        async with sessions.begin() as session:
            if job_id is not None:
                await session.execute(delete(ImportJobModel).where(ImportJobModel.id == job_id))
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
