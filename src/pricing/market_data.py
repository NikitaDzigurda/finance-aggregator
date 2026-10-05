"""Market snapshot ingestion with reviewed source identities and exact prices."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Protocol
from uuid import UUID

from sqlalchemy import func, or_, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from calculation.contracts import CostBasisMethod
from calculation.service import get_position_snapshot, recalculate_portfolio
from imports.models import ImportJobModel, ImportJobStatus, ImportJobType
from instruments.models import (
    InstrumentIdentifierModel,
    InstrumentIdentifierType,
    InstrumentModel,
    InstrumentType,
)
from instruments.service import get_instruments_by_ids
from operations.service import instrument_ids_used_by_operations, portfolio_ids_using_instruments
from pricing.models import MarketMappingModel, MarketPriceModel, MarketPriceUnit
from pricing.service import applicable_market_price, latest_market_prices_for_instruments
from shared.config import Settings
from shared.exact import PRICE_SPEC, ExactDecimalError, validate_decimal


@dataclass(frozen=True)
class MarketQuote:
    symbol: str
    quote_currency: str
    price_unit: MarketPriceUnit
    price_kind: str
    time_quality: str
    price: Decimal
    observed_at: datetime
    fetched_at: datetime


@dataclass(frozen=True)
class MarketSnapshot:
    provider: str
    market: str
    quotes: Sequence[MarketQuote]


class MarketSnapshotProvider(Protocol):
    provider_id: str
    market: str

    async def fetch_snapshot(self) -> MarketSnapshot: ...


class MarketDataConflict(Exception):
    """The provider changed an already stored observation identity."""


class MarketProviderUnavailable(Exception):
    """No provider with verified usage rights is configured for this market."""


@dataclass(frozen=True)
class MarketIngestResult:
    inserted: int
    repeated: int
    skipped_unmapped: int
    skipped_invalid: int
    affected_portfolios: tuple[UUID, ...]
    accepted_instruments: frozenset[UUID]


@dataclass(frozen=True)
class MarketSyncResult:
    ingest: MarketIngestResult
    recalculated_portfolios: tuple[UUID, ...]
    failed_portfolios: tuple[UUID, ...]


def build_market_providers(settings: Settings) -> dict[tuple[str, str], MarketSnapshotProvider]:
    """Register only reviewed public endpoints outside isolated test runs."""
    if settings.environment == "test":
        return {}
    from pricing.coinpaprika import CoinPaprikaSnapshotProvider

    provider = CoinPaprikaSnapshotProvider(
        timeout_seconds=settings.market_http_timeout_seconds,
        max_response_bytes=settings.market_response_max_bytes,
    )
    return {(provider.provider_id, provider.market): provider}


async def ensure_coinpaprika_mappings(
    session: AsyncSession, snapshot: MarketSnapshot
) -> None:
    """Seed only exact reviewed coin identities represented in operations."""
    from pricing.coinpaprika import (
        COINPAPRIKA_ASSETS,
        COINPAPRIKA_MARKET,
        COINPAPRIKA_PRICE_KIND,
        COINPAPRIKA_PROVIDER_ID,
        COINPAPRIKA_TIME_QUALITY,
    )

    if (snapshot.provider, snapshot.market) != (COINPAPRIKA_PROVIDER_ID, COINPAPRIKA_MARKET):
        return
    available = {quote.symbol for quote in snapshot.quotes if _valid_quote(quote)}
    codes = await session.execute(
        select(InstrumentIdentifierModel.instrument_id, InstrumentIdentifierModel.value).where(
            InstrumentIdentifierModel.identifier_type == InstrumentIdentifierType.CRYPTO_ASSET_CODE,
            InstrumentIdentifierModel.value.in_(COINPAPRIKA_ASSETS),
        )
    )
    candidates = list(codes)
    if not candidates:
        return
    instruments = {
        item.id: item
        for item in await get_instruments_by_ids(session, {item[0] for item in candidates})
    }
    used = await instrument_ids_used_by_operations(session, {item[0] for item in candidates})
    existing = set(
        await session.scalars(
            select(MarketMappingModel.instrument_id).where(
                MarketMappingModel.provider == COINPAPRIKA_PROVIDER_ID,
                MarketMappingModel.market == COINPAPRIKA_MARKET,
            )
        )
    )
    for instrument_id, code in candidates:
        coin_id, name = COINPAPRIKA_ASSETS[code]
        instrument = instruments[instrument_id]
        if (
            instrument_id not in used
            or instrument_id in existing
            or coin_id not in available
            or instrument.instrument_type != InstrumentType.CRYPTO_ASSET
            or instrument.currency != "USD"
            or instrument.name not in {code, name}
        ):
            continue
        session.add(
            MarketMappingModel(
                instrument_id=instrument_id,
                provider=COINPAPRIKA_PROVIDER_ID,
                market=COINPAPRIKA_MARKET,
                symbol=coin_id,
                quote_currency="USD",
                identifier_type="crypto_asset_code",
                identifier_value=code,
                price_unit=MarketPriceUnit.CRYPTO_UNIT,
                price_kind=COINPAPRIKA_PRICE_KIND,
                time_quality=COINPAPRIKA_TIME_QUALITY,
                status="verified",
                confirmation_source=(
                    "https://docs.coinpaprika.com/api-reference/tickers/"
                    "get-tickers-for-all-active-coins"
                ),
                confirmed_at=datetime.now(UTC),
            )
        )
        existing.add(instrument_id)
    await session.commit()


async def enqueue_market_data_sync(
    session: AsyncSession,
    *,
    provider: str,
    market: str,
    minimum_interval_seconds: int,
) -> tuple[ImportJobModel, bool]:
    """Coalesce requests for one global snapshot across concurrent workers."""
    await session.execute(select(func.pg_advisory_xact_lock(47102, 2)))
    threshold = datetime.now(UTC) - timedelta(seconds=minimum_interval_seconds)
    existing = await session.scalar(
        select(ImportJobModel)
        .where(
            ImportJobModel.job_type == ImportJobType.MARKET_DATA_SYNC,
            ImportJobModel.payload["provider"].as_string() == provider,
            ImportJobModel.payload["market"].as_string() == market,
            or_(
                ImportJobModel.status.in_((ImportJobStatus.PENDING, ImportJobStatus.RUNNING)),
                ImportJobModel.created_at >= threshold,
            ),
        )
        .order_by(ImportJobModel.created_at.desc(), ImportJobModel.id.desc())
        .limit(1)
    )
    if existing is not None:
        return existing, True
    job = ImportJobModel(
        batch_id=None,
        job_type=ImportJobType.MARKET_DATA_SYNC,
        status=ImportJobStatus.PENDING,
        payload={"provider": provider, "market": market},
    )
    session.add(job)
    await session.commit()
    await session.refresh(job)
    return job, False


async def get_latest_market_sync_job(
    session: AsyncSession, *, provider: str, market: str
) -> ImportJobModel | None:
    result = await session.scalars(
        select(ImportJobModel)
        .where(
            ImportJobModel.job_type == ImportJobType.MARKET_DATA_SYNC,
            ImportJobModel.payload["provider"].as_string() == provider,
            ImportJobModel.payload["market"].as_string() == market,
        )
        .order_by(ImportJobModel.created_at.desc(), ImportJobModel.id.desc())
        .limit(1)
    )
    return result.one_or_none()


def _valid_quote(quote: MarketQuote) -> bool:
    if not isinstance(quote.price, Decimal):
        return False
    try:
        validate_decimal(quote.price, PRICE_SPEC)
    except ExactDecimalError:
        return False
    if (
        quote.price <= 0
        or quote.observed_at.tzinfo is None
        or quote.fetched_at.tzinfo is None
        or quote.observed_at > quote.fetched_at + timedelta(minutes=5)
        or quote.time_quality not in {"provider_snapshot", "provider_trade"}
        or not quote.price_kind.strip()
        or len(quote.price_kind) > 32
        or len(quote.symbol) > 64
        or not quote.symbol.strip()
        or len(quote.quote_currency) != 3
        or not quote.quote_currency.isascii()
        or not quote.quote_currency.isupper()
    ):
        return False
    return True


def mapping_matches_instrument(mapping: MarketMappingModel, instrument: InstrumentModel) -> bool:
    if instrument.currency != mapping.quote_currency:
        return False
    if mapping.price_unit == MarketPriceUnit.SECURITY_UNIT:
        if instrument.instrument_type not in {
            InstrumentType.STOCK,
            InstrumentType.ETF,
            InstrumentType.FUND,
        }:
            return False
        expected_type = InstrumentIdentifierType.ISIN
    elif mapping.price_unit == MarketPriceUnit.CRYPTO_UNIT:
        if (
            instrument.instrument_type != InstrumentType.CRYPTO_ASSET
            or mapping.quote_currency != "USD"
        ):
            return False
        expected_type = InstrumentIdentifierType.CRYPTO_ASSET_CODE
    else:
        return False
    return mapping.identifier_type == expected_type.value and any(
        value.identifier_type == expected_type and value.value == mapping.identifier_value
        for value in instrument.identifiers
    )


async def list_market_mappings(
    session: AsyncSession, instrument_id: UUID
) -> list[MarketMappingModel]:
    return list(
        await session.scalars(
            select(MarketMappingModel)
            .where(MarketMappingModel.instrument_id == instrument_id)
            .order_by(MarketMappingModel.provider, MarketMappingModel.market, MarketMappingModel.id)
        )
    )


async def ingest_market_snapshot(
    session: AsyncSession, snapshot: MarketSnapshot
) -> MarketIngestResult:
    """Persist only exact quotes with a reviewed mapping and a portfolio use."""
    if (
        not snapshot.provider
        or not snapshot.market
        or len(snapshot.provider) > 64
        or len(snapshot.market) > 32
        or len(snapshot.quotes) > 100_000
    ):
        raise ValueError("Market snapshot envelope is invalid")
    mappings = list(
        await session.scalars(
            select(MarketMappingModel).where(
                MarketMappingModel.provider == snapshot.provider,
                MarketMappingModel.market == snapshot.market,
                MarketMappingModel.status == "verified",
            )
        )
    )
    instruments = {
        item.id: item
        for item in await get_instruments_by_ids(
            session, {mapping.instrument_id for mapping in mappings}
        )
    }
    used_instruments = await instrument_ids_used_by_operations(
        session, {mapping.instrument_id for mapping in mappings}
    )
    mapping_by_code = {
        (
            mapping.symbol,
            mapping.quote_currency,
            mapping.price_unit,
            mapping.price_kind,
            mapping.time_quality,
        ): mapping
        for mapping in mappings
        if mapping.instrument_id in used_instruments
        and mapping_matches_instrument(mapping, instruments[mapping.instrument_id])
    }
    inserted = repeated = skipped_unmapped = skipped_invalid = 0
    accepted_instruments: set[UUID] = set()
    try:
        for quote in snapshot.quotes:
            if not _valid_quote(quote):
                skipped_invalid += 1
                continue
            mapping = mapping_by_code.get(
                (
                    quote.symbol,
                    quote.quote_currency,
                    quote.price_unit,
                    quote.price_kind,
                    quote.time_quality,
                )
            )
            if mapping is None:
                skipped_unmapped += 1
                continue
            values = {
                "instrument_id": mapping.instrument_id,
                "mapping_id": mapping.id,
                "price": quote.price,
                "currency": quote.quote_currency,
                "observed_at": quote.observed_at,
                "fetched_at": quote.fetched_at,
                "source": "automatic",
                "provider": snapshot.provider,
                "price_kind": quote.price_kind,
                "time_quality": quote.time_quality,
            }
            result = await session.execute(
                insert(MarketPriceModel)
                .values(**values)
                .on_conflict_do_nothing(constraint="uq_market_prices_observation_identity")
                .returning(MarketPriceModel.id)
            )
            if result.scalar_one_or_none() is not None:
                inserted += 1
                accepted_instruments.add(mapping.instrument_id)
                continue
            existing = await session.scalar(
                select(MarketPriceModel).where(
                    MarketPriceModel.instrument_id == mapping.instrument_id,
                    MarketPriceModel.currency == quote.quote_currency,
                    MarketPriceModel.observed_at == quote.observed_at,
                    MarketPriceModel.provider == snapshot.provider,
                    MarketPriceModel.price_kind == quote.price_kind,
                )
            )
            if (
                existing is None
                or existing.mapping_id != mapping.id
                or existing.price != quote.price
                or existing.time_quality != quote.time_quality
            ):
                raise MarketDataConflict("Existing market observation conflicts with provider data")
            repeated += 1
            accepted_instruments.add(mapping.instrument_id)
        await session.commit()
    except Exception:
        await session.rollback()
        raise
    affected = await portfolio_ids_using_instruments(session, accepted_instruments)
    return MarketIngestResult(
        inserted,
        repeated,
        skipped_unmapped,
        skipped_invalid,
        tuple(affected),
        frozenset(accepted_instruments),
    )


async def synchronize_market_snapshot(
    sessions: async_sessionmaker[AsyncSession], provider: MarketSnapshotProvider
) -> MarketSyncResult:
    snapshot = await provider.fetch_snapshot()
    if snapshot.provider != provider.provider_id or snapshot.market != provider.market:
        raise ValueError("Provider returned an unexpected market snapshot")
    async with sessions() as session:
        await ensure_coinpaprika_mappings(session, snapshot)
        ingest = await ingest_market_snapshot(session, snapshot)
    completed: list[UUID] = []
    failed: list[UUID] = []
    for portfolio_id in ingest.affected_portfolios:
        async with sessions() as session:
            previous = await get_position_snapshot(session, portfolio_id)
            if previous is not None:
                affected_positions = [
                    position
                    for position in previous.positions
                    if position.instrument_id in ingest.accepted_instruments
                ]
                if not affected_positions:
                    # The last saved snapshot may predate an operation in this asset.
                    # Recalculate the current valuation rather than keeping it empty.
                    affected_positions = []
                current_prices = await latest_market_prices_for_instruments(
                    session,
                    instrument_ids=ingest.accepted_instruments,
                    valuation_as_of=datetime.now(UTC),
                )
                if affected_positions and all(
                    (
                        selected.id
                        if (
                            selected := applicable_market_price(
                                current_prices,
                                instrument_id=position.instrument_id,
                                cost_currency=position.cost_currency,
                            )
                        )
                        is not None
                        else None
                    )
                    == position.market_price_observation_id
                    for position in affected_positions
                ):
                    continue
            method = (
                previous.cost_basis_method
                if previous is not None
                else CostBasisMethod.WEIGHTED_AVERAGE
            )
            try:
                await recalculate_portfolio(
                    session,
                    portfolio_id=portfolio_id,
                    as_of=datetime.now(UTC),
                    cost_basis_method=method,
                )
                completed.append(portfolio_id)
            except Exception:
                await session.rollback()
                failed.append(portfolio_id)
    return MarketSyncResult(ingest, tuple(completed), tuple(failed))
