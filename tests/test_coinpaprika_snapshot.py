"""Synthetic contract checks for the public crypto snapshot boundary."""

import os
from collections.abc import AsyncIterator
from datetime import UTC, datetime
from decimal import Decimal
from uuid import UUID

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from apps.api.main import create_app
from pricing.coinpaprika import (
    COINPAPRIKA_URL,
    CoinPaprikaProviderError,
    CoinPaprikaSnapshotProvider,
    parse_coinpaprika_snapshot,
)
from pricing.market_data import enqueue_market_data_sync, synchronize_market_snapshot
from shared.database import get_db_session


def _payload(*, price: str = "123.123456789012345678", symbol: str = "BTC") -> bytes:
    return (
        '[{"id":"btc-bitcoin","name":"Bitcoin","symbol":"'
        + symbol
        + '","last_updated":"2026-09-15T11:00:00Z",'
        + '"quotes":{"USD":{"price":'
        + price
        + '}}},{"id":"unrelated-synthetic-coin","name":"Unrelated",'
        + '"symbol":"XYZ","last_updated":"2026-09-15T11:00:00Z",'
        + '"quotes":{"USDT":{"price":999}}}]'
    ).encode()


def test_coinpaprika_exact_usd_unit_time_and_identity() -> None:
    snapshot = parse_coinpaprika_snapshot(
        _payload(), fetched_at=datetime(2026, 9, 15, 11, 1, tzinfo=UTC)
    )
    assert len(snapshot.quotes) == 1
    quote = snapshot.quotes[0]
    assert quote.symbol == "btc-bitcoin"
    assert quote.quote_currency == "USD"
    assert quote.price == Decimal("123.123456789012345678")
    assert quote.observed_at == datetime(2026, 9, 15, 11, tzinfo=UTC)
    assert quote.price_kind == "global_aggregate"
    with pytest.raises(CoinPaprikaProviderError):
        parse_coinpaprika_snapshot(
            _payload(symbol="XBTC"), fetched_at=datetime(2026, 9, 15, 11, 1, tzinfo=UTC)
        )


@pytest.mark.asyncio
async def test_coinpaprika_requests_only_the_common_public_snapshot() -> None:
    requests: list[tuple[str, float, int]] = []

    async def fetcher(url: str, timeout_seconds: float, max_bytes: int) -> bytes:
        requests.append((url, timeout_seconds, max_bytes))
        return _payload(price="123")

    provider = CoinPaprikaSnapshotProvider(
        timeout_seconds=5, max_response_bytes=4 * 1024 * 1024, fetcher=fetcher
    )
    await provider.fetch_snapshot()
    assert requests == [(COINPAPRIKA_URL, 5, 4 * 1024 * 1024)]


@pytest.mark.postgres
@pytest.mark.asyncio
async def test_curated_coin_id_creates_mapping_and_recalculates_exact_value() -> None:
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
    try:
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            portfolio = await client.post(
                "/api/v1/portfolios",
                json={"name": "Synthetic CoinPaprika valuation", "base_currency": "USD"},
            )
            assert portfolio.status_code == 201, portfolio.text
            portfolio_id = UUID(portfolio.json()["id"])
            account = await client.post(
                f"/api/v1/portfolios/{portfolio_id}/accounts",
                json={"name": "Synthetic CEX", "account_type": "cex"},
            )
            assert account.status_code == 201, account.text
            instrument = await client.post(
                "/api/v1/instruments",
                json={
                    "name": "BTC",
                    "instrument_type": "crypto_asset",
                    "currency": "USD",
                    "identifiers": [{"identifier_type": "crypto_asset_code", "value": "BTC"}],
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
            initial = await client.post(
                f"/api/v1/portfolios/{portfolio_id}/positions/recalculate",
                json={"as_of": "2026-09-02T09:00:00Z"},
            )
            assert initial.status_code == 200, initial.text

            async def fetcher(url: str, timeout_seconds: float, max_bytes: int) -> bytes:
                assert url == COINPAPRIKA_URL
                return _payload()

            provider = CoinPaprikaSnapshotProvider(
                timeout_seconds=5, max_response_bytes=4 * 1024 * 1024, fetcher=fetcher
            )
            result = await synchronize_market_snapshot(sessions, provider)
            assert result.ingest.inserted == 1
            assert result.recalculated_portfolios == (portfolio_id,)
            saved = await client.get(f"/api/v1/portfolios/{portfolio_id}/positions")
            assert saved.status_code == 200, saved.text
            position = saved.json()["positions"][0]
            assert position["market_value"] == "246.246913578024691356"
            assert position["cost_basis"] == "20.000000000000000000"
            mappings = await client.get(f"/api/v1/instruments/{instrument_id}/market-mappings")
            assert mappings.json()["items"][0]["symbol"] == "btc-bitcoin"
            assert mappings.json()["items"][0]["eligible"] is True

            fresh = await client.get(
                "/api/v1/prices/resolve",
                params={
                    "instrument_id": str(instrument_id),
                    "currency": "USD",
                    "valuation_as_of": "2026-09-15T12:00:00Z",
                },
            )
            stale = await client.get(
                "/api/v1/prices/resolve",
                params={
                    "instrument_id": str(instrument_id),
                    "currency": "USD",
                    "valuation_as_of": "2026-09-15T14:00:00Z",
                },
            )
            assert fresh.json()["status"] == "fresh"
            assert stale.json()["status"] == "stale"

            async with sessions() as session:
                await enqueue_market_data_sync(
                    session, provider="coinpaprika", market="GLOBAL", minimum_interval_seconds=60
                )
            latest_job = await client.get("/api/v1/market-data/sync/latest")
            assert latest_job.status_code == 200, latest_job.text
            assert latest_job.json()["provider"] == "coinpaprika"
            assert latest_job.json()["status"] == "pending"
            assert latest_job.json()["counters"] is None

            repeated = await synchronize_market_snapshot(sessions, provider)
            assert repeated.ingest.inserted == 0
            assert repeated.ingest.repeated == 1
    finally:
        app.dependency_overrides.clear()
        await engine.dispose()
