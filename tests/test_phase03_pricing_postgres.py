import asyncio
import os
from collections.abc import AsyncIterator
from datetime import UTC, datetime
from decimal import Decimal
from uuid import UUID, uuid4

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from apps.api.main import create_app
from imports.models import ImportJobModel
from instruments.models import InstrumentModel
from pricing.fx import FxProviderError, FxRateObservation, FxRateProvider
from pricing.models import ExchangeRateMode, ExchangeRateModel, MarketPriceModel
from pricing.service import enqueue_fx_sync, synchronize_usd_rub
from shared.database import get_db_session


class _SyntheticProvider(FxRateProvider):
    provider_id = "synthetic_cbr_test"
    source_id = "official_daily_shape"

    async def fetch_rate(
        self,
        *,
        base_currency: str,
        quote_currency: str,
    ) -> FxRateObservation:
        return FxRateObservation(
            base_currency=base_currency,
            quote_currency=quote_currency,
            rate=Decimal("81.25"),
            observed_at=datetime(2099, 1, 3, tzinfo=UTC),
            fetched_at=datetime(2099, 1, 2, 18, tzinfo=UTC),
            provider=self.provider_id,
            source=self.source_id,
            mode=ExchangeRateMode.AUTOMATIC,
        )


class _FailingProvider(FxRateProvider):
    provider_id = "synthetic_cbr_test"
    source_id = "official_daily_shape"

    async def fetch_rate(
        self,
        *,
        base_currency: str,
        quote_currency: str,
    ) -> FxRateObservation:
        del base_currency, quote_currency
        raise FxProviderError("fx_provider_unavailable", "Public FX provider is unavailable")


@pytest.mark.postgres
@pytest.mark.asyncio
async def test_phase03_manual_pricing_fx_resolution_and_sync_are_atomic() -> None:
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
    instrument_id: UUID | None = None
    created_job_id: UUID | None = None
    manual_times = (
        datetime(2099, 1, 1, tzinfo=UTC),
        datetime(2099, 1, 5, tzinfo=UTC),
        datetime(2099, 1, 7, tzinfo=UTC),
    )

    try:
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            instrument = await client.post(
                "/api/v1/instruments",
                json={
                    "name": f"Synthetic Phase03 {suffix}",
                    "instrument_type": "stock",
                    "currency": "USD",
                    "identifiers": [
                        {
                            "identifier_type": "provider_code",
                            "value": suffix,
                            "provider": "phase03_synthetic",
                        }
                    ],
                },
            )
            assert instrument.status_code == 201, instrument.text
            instrument_id = UUID(instrument.json()["id"])

            rejected_prices = await client.post(
                "/api/v1/prices/batch",
                json={
                    "items": [
                        {
                            "instrument_id": str(instrument_id),
                            "price": "100.125",
                            "currency": "USD",
                            "observed_at": "2099-01-01T00:00:00Z",
                        },
                        {
                            "instrument_id": str(uuid4()),
                            "price": "50",
                            "currency": "USD",
                            "observed_at": "2099-01-01T00:00:00Z",
                        },
                    ]
                },
            )
            assert rejected_prices.status_code == 404
            empty_prices = await client.get(
                "/api/v1/prices",
                params={"instrument_id": str(instrument_id)},
            )
            assert empty_prices.status_code == 200
            assert empty_prices.json()["items"] == []

            accepted_prices = await client.post(
                "/api/v1/prices/batch",
                json={
                    "items": [
                        {
                            "instrument_id": str(instrument_id),
                            "price": "100.125",
                            "currency": "USD",
                            "observed_at": "2099-01-01T00:00:00Z",
                        },
                        {
                            "instrument_id": str(instrument_id),
                            "price": "999",
                            "currency": "USD",
                            "observed_at": "2099-01-05T00:00:00Z",
                        },
                    ]
                },
            )
            assert accepted_prices.status_code == 201, accepted_prices.text
            assert accepted_prices.json()["items"][0]["price"] == ("100.125000000000000000")
            assert accepted_prices.json()["items"][0]["source"] == "manual"
            resolved_price = await client.get(
                "/api/v1/prices/resolve",
                params={
                    "instrument_id": str(instrument_id),
                    "valuation_as_of": "2099-01-03T00:00:00Z",
                },
            )
            assert resolved_price.status_code == 200, resolved_price.text
            assert resolved_price.json()["observation"]["price"] == ("100.125000000000000000")
            assert resolved_price.json()["age_seconds"] == 172800

            rates = await client.post(
                "/api/v1/fx-rates/batch",
                json={
                    "items": [
                        {
                            "base_currency": "USD",
                            "quote_currency": "RUB",
                            "rate": "80",
                            "observed_at": "2099-01-01T00:00:00Z",
                        },
                        {
                            "base_currency": "USD",
                            "quote_currency": "RUB",
                            "rate": "99",
                            "observed_at": "2099-01-05T00:00:00Z",
                        },
                    ]
                },
            )
            assert rates.status_code == 201, rates.text
            assert all(item["mode"] == "manual" for item in rates.json()["items"])

            rejected_rates = await client.post(
                "/api/v1/fx-rates/batch",
                json={
                    "items": [
                        {
                            "base_currency": "EUR",
                            "quote_currency": "RUB",
                            "rate": "100",
                            "observed_at": "2099-01-07T00:00:00Z",
                        },
                        {
                            "base_currency": "USD",
                            "quote_currency": "USD",
                            "rate": "1",
                            "observed_at": "2099-01-07T00:00:00Z",
                        },
                    ]
                },
            )
            assert rejected_rates.status_code == 422
            list_rejected = await client.get(
                "/api/v1/fx-rates",
                params={"observed_from": "2099-01-07T00:00:00Z"},
            )
            assert list_rejected.status_code == 200
            assert list_rejected.json()["items"] == []

            direct = await client.get(
                "/api/v1/fx-rates/resolve",
                params={
                    "base_currency": "USD",
                    "quote_currency": "RUB",
                    "valuation_as_of": "2099-01-03T00:00:00Z",
                },
            )
            assert direct.status_code == 200, direct.text
            assert direct.json()["rate"] == "80.000000000000000000000000"
            assert direct.json()["path"] == "direct"
            assert direct.json()["observations"][0]["age_seconds"] == 172800

            inverse = await client.get(
                "/api/v1/fx-rates/resolve",
                params={
                    "base_currency": "RUB",
                    "quote_currency": "USD",
                    "valuation_as_of": "2099-01-03T00:00:00Z",
                },
            )
            assert inverse.status_code == 200, inverse.text
            assert inverse.json()["rate"] == "0.012500000000000000000000"
            assert inverse.json()["path"] == "inverse"

        async with sessions() as session:
            first, duplicate = await synchronize_usd_rub(session, _SyntheticProvider())
            assert duplicate is False
            repeated, duplicate = await synchronize_usd_rub(session, _SyntheticProvider())
            assert duplicate is True
            assert repeated.id == first.id
            with pytest.raises(FxProviderError):
                await synchronize_usd_rub(session, _FailingProvider())
            stored_rate = await session.scalar(
                select(ExchangeRateModel.rate).where(
                    ExchangeRateModel.provider == _SyntheticProvider.provider_id,
                    ExchangeRateModel.observed_at == datetime(2099, 1, 3, tzinfo=UTC),
                )
            )
            assert stored_rate == Decimal("81.250000000000000000000000")
            count = await session.scalar(
                select(func.count())
                .select_from(ExchangeRateModel)
                .where(ExchangeRateModel.provider == _SyntheticProvider.provider_id)
            )
            assert count == 1

            job, duplicate = await enqueue_fx_sync(session, minimum_interval_seconds=60)
            if not duplicate:
                created_job_id = job.id
            repeated_job, repeated_duplicate = await enqueue_fx_sync(
                session, minimum_interval_seconds=60
            )
            assert repeated_duplicate is True
            assert repeated_job.id == job.id
            assert job.batch_id is None
            assert job.payload == {"pairs": ["USD/RUB"]}
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            latest = await client.get("/api/v1/fx-rates/sync")
            assert latest.status_code == 200, latest.text
            assert latest.json()["id"] == str(job.id)

        async def enqueue_concurrently():
            async with sessions() as session:
                queued, duplicate = await enqueue_fx_sync(session, minimum_interval_seconds=3600)
                return queued.id, duplicate

        concurrent = await asyncio.wait_for(
            asyncio.gather(enqueue_concurrently(), enqueue_concurrently()), timeout=5
        )
        assert concurrent == [(job.id, True), (job.id, True)]
    finally:
        async with sessions.begin() as session:
            if created_job_id is not None:
                await session.execute(
                    delete(ImportJobModel).where(ImportJobModel.id == created_job_id)
                )
            await session.execute(
                delete(ExchangeRateModel).where(
                    (
                        (ExchangeRateModel.provider == "manual")
                        & (ExchangeRateModel.observed_at.in_(manual_times))
                    )
                    | (ExchangeRateModel.provider == _SyntheticProvider.provider_id)
                )
            )
            if instrument_id is not None:
                await session.execute(
                    delete(MarketPriceModel).where(MarketPriceModel.instrument_id == instrument_id)
                )
                await session.execute(
                    delete(InstrumentModel).where(InstrumentModel.id == instrument_id)
                )
        application.dependency_overrides.clear()
        await engine.dispose()
