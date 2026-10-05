"""Synthetic first-slice market-price load benchmark on a dedicated test database."""

from __future__ import annotations

import asyncio
import os
from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from time import monotonic
from uuid import UUID

from httpx import ASGITransport, AsyncClient
from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from apps.api.main import create_app
from pricing.market_data import MarketQuote, MarketSnapshot, synchronize_market_snapshot
from pricing.models import MarketMappingModel, MarketPriceModel, MarketPriceUnit
from pricing.service import latest_market_prices_for_instruments
from shared.database import get_db_session


async def main() -> None:
    database_url = os.environ.get("FINANCE_TEST_DATABASE_URL", "")
    if "finance_phase05_bench_test_" not in database_url:
        raise RuntimeError("Use a dedicated finance_phase05_bench_test_ database")
    engine = create_async_engine(database_url)
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    app = create_app()

    async def test_db_session() -> AsyncIterator[AsyncSession]:
        async with sessions() as session:
            yield session

    app.dependency_overrides[get_db_session] = test_db_session
    codes = {
        "BTC": "btc-bitcoin",
        "ETH": "eth-ethereum",
        "SOL": "sol-solana",
        "USDT": "usdt-tether",
        "USDC": "usdc-usd-coin",
    }
    instrument_ids: dict[str, UUID] = {}
    mapping_ids: dict[str, UUID] = {}
    try:
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            portfolio = await client.post(
                "/api/v1/portfolios",
                json={"name": "Synthetic Phase05 large load", "base_currency": "USD"},
            )
            assert portfolio.status_code == 201, portfolio.text
            portfolio_id = UUID(portfolio.json()["id"])
            account = await client.post(
                f"/api/v1/portfolios/{portfolio_id}/accounts",
                json={"name": "Synthetic CEX", "account_type": "cex"},
            )
            assert account.status_code == 201, account.text
            for code in codes:
                instrument = await client.post(
                    "/api/v1/instruments",
                    json={
                        "name": code,
                        "instrument_type": "crypto_asset",
                        "currency": "USD",
                        "identifiers": [
                            {"identifier_type": "crypto_asset_code", "value": code}
                        ],
                    },
                )
                assert instrument.status_code == 201, instrument.text
                instrument_ids[code] = UUID(instrument.json()["id"])
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
                            "instrument_id": str(instrument_ids[code]),
                            "quantity": "1",
                            "price": "10",
                            "price_currency": "USD",
                        },
                    },
                )
                assert operation.status_code == 201, operation.text
            initial = await client.post(
                f"/api/v1/portfolios/{portfolio_id}/positions/recalculate",
                json={"as_of": datetime.now(UTC).isoformat()},
            )
            assert initial.status_code == 200, initial.text

        async with sessions.begin() as session:
            for code, symbol in codes.items():
                mapping = MarketMappingModel(
                    instrument_id=instrument_ids[code],
                    provider="coinpaprika",
                    market="GLOBAL",
                    symbol=symbol,
                    quote_currency="USD",
                    identifier_type="crypto_asset_code",
                    identifier_value=code,
                    price_unit="crypto_unit",
                    price_kind="global_aggregate",
                    time_quality="provider_snapshot",
                    status="verified",
                    confirmation_source="synthetic_large_load_fixture",
                    confirmed_at=datetime.now(UTC),
                )
                session.add(mapping)
                await session.flush()
                mapping_ids[code] = mapping.id
        async with sessions() as session:
            before_bytes = await session.scalar(
                text("SELECT pg_total_relation_size('market_prices')")
            )

        base = datetime.now(UTC) - timedelta(days=365)
        start = monotonic()
        async with sessions.begin() as session:
            for code in codes:
                await session.execute(
                    text(
                        "INSERT INTO market_prices "
                        "(id, instrument_id, mapping_id, price, currency, observed_at, "
                        "fetched_at, source, provider, price_kind, time_quality) "
                        "SELECT gen_random_uuid(), :instrument_id, :mapping_id, "
                        "100.123456789012345678, 'USD', "
                        ":base_time + make_interval(hours => n), "
                        ":base_time + make_interval(hours => n, mins => 1), "
                        "'automatic', 'coinpaprika', 'global_aggregate', "
                        "'provider_snapshot' FROM generate_series(0, 8759) AS n"
                    ),
                    {
                        "instrument_id": instrument_ids[code],
                        "mapping_id": mapping_ids[code],
                        "base_time": base,
                    },
                )
        insert_seconds = monotonic() - start

        start = monotonic()
        async with sessions() as session:
            selected = await latest_market_prices_for_instruments(
                session,
                instrument_ids=set(instrument_ids.values()),
                valuation_as_of=datetime.now(UTC),
            )
        lookup_seconds = monotonic() - start
        assert len(selected) == 5

        class SyntheticProvider:
            provider_id = "coinpaprika"
            market = "GLOBAL"

            async def fetch_snapshot(self) -> MarketSnapshot:
                fetched_at = datetime.now(UTC)
                observed_at = fetched_at - timedelta(minutes=1)
                return MarketSnapshot(
                    self.provider_id,
                    self.market,
                    tuple(
                        MarketQuote(
                            symbol=symbol,
                            quote_currency="USD",
                            price_unit=MarketPriceUnit.CRYPTO_UNIT,
                            price_kind="global_aggregate",
                            time_quality="provider_snapshot",
                            price=Decimal("101.123456789012345678"),
                            observed_at=observed_at,
                            fetched_at=fetched_at,
                        )
                        for symbol in codes.values()
                    ),
                )

        start = monotonic()
        result = await synchronize_market_snapshot(sessions, SyntheticProvider())
        sync_seconds = monotonic() - start
        assert result.ingest.inserted == 5
        assert result.recalculated_portfolios == (portfolio_id,)
        async with sessions() as session:
            after_bytes = await session.scalar(
                text("SELECT pg_total_relation_size('market_prices')")
            )
            rows = await session.scalar(select(func.count()).select_from(MarketPriceModel))
        assert rows == 43_805
        print(
            {
                "synthetic_rows": rows,
                "relation_bytes_before": int(before_bytes or 0),
                "relation_bytes_after": int(after_bytes or 0),
                "bulk_insert_seconds": round(insert_seconds, 3),
                "latest_lookup_seconds": round(lookup_seconds, 3),
                "five_quote_sync_and_recalc_seconds": round(sync_seconds, 3),
            }
        )
    finally:
        app.dependency_overrides.clear()
        await engine.dispose()


if __name__ == "__main__":
    asyncio.run(main())
