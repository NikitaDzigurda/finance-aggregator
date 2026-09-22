from __future__ import annotations

from collections.abc import Collection, Sequence
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID

from sqlalchemy import Select, and_, case, exists, func, or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.sql.elements import ColumnElement

from imports.models import ImportJobModel, ImportJobStatus, ImportJobType
from instruments.models import InstrumentIdentifierModel, InstrumentModel, InstrumentType
from pricing.fx import (
    CbrDailyFxRateProvider,
    FxRateCandidate,
    FxRateObservation,
    FxRateProvider,
    ResolvedFxRate,
    resolve_fx_rate,
)
from pricing.models import ExchangeRateMode, ExchangeRateModel, MarketMappingModel, MarketPriceModel
from pricing.schemas import ExchangeRateCreate, MarketPriceCreate
from shared.config import Settings


def create_market_price(payload: MarketPriceCreate) -> MarketPriceModel:
    return MarketPriceModel(
        instrument_id=payload.instrument_id,
        price=payload.price,
        currency=payload.currency,
        observed_at=payload.observed_at,
        fetched_at=datetime.now(UTC),
        source="manual",
        provider="manual",
        price_kind="manual",
        time_quality="user_supplied",
    )


def market_price_stale_after_seconds(price: MarketPriceModel, settings: Settings) -> int:
    """Crypto aggregate observations age continuously; manual prices retain their policy."""
    if price.provider == "coinpaprika" and price.price_kind == "global_aggregate":
        return settings.crypto_aggregate_stale_after_seconds
    return settings.market_price_stale_after_seconds


def create_market_prices(payloads: Sequence[MarketPriceCreate]) -> list[MarketPriceModel]:
    return [create_market_price(payload) for payload in payloads]


async def list_market_prices(
    session: AsyncSession,
    *,
    instrument_id: UUID | None,
    observed_from: datetime | None,
    observed_to: datetime | None,
    limit: int,
    offset: int,
) -> list[MarketPriceModel]:
    statement: Select[tuple[MarketPriceModel]] = select(MarketPriceModel)
    if instrument_id is not None:
        statement = statement.where(MarketPriceModel.instrument_id == instrument_id)
    if observed_from is not None:
        statement = statement.where(MarketPriceModel.observed_at >= observed_from)
    if observed_to is not None:
        statement = statement.where(MarketPriceModel.observed_at <= observed_to)
    statement = (
        statement.order_by(MarketPriceModel.observed_at.desc(), MarketPriceModel.id.desc())
        .limit(limit)
        .offset(offset)
    )
    return list(await session.scalars(statement))


async def resolve_market_price(
    session: AsyncSession,
    *,
    instrument_id: UUID,
    currency: str | None,
    valuation_as_of: datetime,
) -> MarketPriceModel | None:
    statement: Select[tuple[MarketPriceModel]] = _eligible_price_query().where(
        MarketPriceModel.instrument_id == instrument_id,
        MarketPriceModel.observed_at <= valuation_as_of,
    )
    if currency is not None:
        statement = statement.where(MarketPriceModel.currency == currency)
    statement = statement.order_by(*_price_order()).limit(1)
    result = await session.scalars(statement)
    return result.one_or_none()


def _price_order() -> tuple[ColumnElement[Any], ...]:
    return (
        MarketPriceModel.observed_at.desc(),
        case((MarketPriceModel.source == "manual", 1), else_=0).desc(),
        MarketPriceModel.id.desc(),
    )


def _eligible_price_query() -> Select[tuple[MarketPriceModel]]:
    matching_identifier = exists(
        select(1)
        .select_from(InstrumentIdentifierModel)
        .where(
            InstrumentIdentifierModel.instrument_id == InstrumentModel.id,
            InstrumentIdentifierModel.identifier_type == MarketMappingModel.identifier_type,
            InstrumentIdentifierModel.value == MarketMappingModel.identifier_value,
        )
    )
    matching_asset = or_(
        and_(
            MarketMappingModel.price_unit == "security_unit",
            MarketMappingModel.identifier_type == "isin",
            InstrumentModel.instrument_type.in_(
                (InstrumentType.STOCK, InstrumentType.ETF, InstrumentType.FUND)
            ),
        ),
        and_(
            MarketMappingModel.price_unit == "crypto_unit",
            MarketMappingModel.identifier_type == "crypto_asset_code",
            InstrumentModel.instrument_type == InstrumentType.CRYPTO_ASSET,
            MarketMappingModel.quote_currency == "USD",
        ),
    )
    return (
        select(MarketPriceModel)
        .outerjoin(MarketMappingModel, MarketPriceModel.mapping_id == MarketMappingModel.id)
        .outerjoin(InstrumentModel, MarketPriceModel.instrument_id == InstrumentModel.id)
        .where(
            or_(
                MarketPriceModel.source == "manual",
                and_(
                    MarketPriceModel.source == "automatic",
                    MarketMappingModel.status == "verified",
                    MarketMappingModel.instrument_id == MarketPriceModel.instrument_id,
                    MarketMappingModel.provider == MarketPriceModel.provider,
                    MarketMappingModel.quote_currency == MarketPriceModel.currency,
                    MarketMappingModel.price_kind == MarketPriceModel.price_kind,
                    MarketMappingModel.time_quality == MarketPriceModel.time_quality,
                    InstrumentModel.currency == MarketPriceModel.currency,
                    matching_asset,
                    matching_identifier,
                ),
            )
        )
    )


async def latest_market_prices_for_instruments(
    session: AsyncSession,
    *,
    instrument_ids: Collection[UUID],
    valuation_as_of: datetime,
) -> list[MarketPriceModel]:
    """Return one deterministic eligible observation per instrument and currency."""
    if not instrument_ids:
        return []
    statement = (
        _eligible_price_query()
        .where(
            MarketPriceModel.instrument_id.in_(instrument_ids),
            MarketPriceModel.observed_at <= valuation_as_of,
        )
        .distinct(MarketPriceModel.instrument_id, MarketPriceModel.currency)
        .order_by(
            MarketPriceModel.instrument_id,
            MarketPriceModel.currency,
            *_price_order(),
        )
    )
    return list(await session.scalars(statement))


def applicable_market_price(
    records: Sequence[MarketPriceModel],
    *,
    instrument_id: UUID,
    cost_currency: str | None,
) -> MarketPriceModel | None:
    matching = [item for item in records if item.instrument_id == instrument_id]
    if cost_currency is not None:
        preferred = next((item for item in matching if item.currency == cost_currency), None)
        if preferred is not None:
            return preferred
    return matching[0] if len(matching) == 1 else None


async def get_market_prices_by_ids(
    session: AsyncSession, ids: Collection[UUID]
) -> dict[UUID, MarketPriceModel]:
    if not ids:
        return {}
    records = await session.scalars(select(MarketPriceModel).where(MarketPriceModel.id.in_(ids)))
    return {item.id: item for item in records}


def create_manual_exchange_rates(
    payloads: Sequence[ExchangeRateCreate],
    *,
    fetched_at: datetime | None = None,
) -> list[ExchangeRateModel]:
    fetched = fetched_at or datetime.now(UTC)
    return [
        ExchangeRateModel(
            base_currency=payload.base_currency,
            quote_currency=payload.quote_currency,
            rate=payload.rate,
            observed_at=payload.observed_at,
            fetched_at=fetched,
            provider="manual",
            source="swagger_batch",
            mode=ExchangeRateMode.MANUAL,
        )
        for payload in payloads
    ]


async def list_exchange_rates(
    session: AsyncSession,
    *,
    base_currency: str | None,
    quote_currency: str | None,
    observed_from: datetime | None,
    observed_to: datetime | None,
    limit: int,
    offset: int,
) -> list[ExchangeRateModel]:
    statement: Select[tuple[ExchangeRateModel]] = select(ExchangeRateModel)
    if base_currency is not None:
        statement = statement.where(ExchangeRateModel.base_currency == base_currency)
    if quote_currency is not None:
        statement = statement.where(ExchangeRateModel.quote_currency == quote_currency)
    if observed_from is not None:
        statement = statement.where(ExchangeRateModel.observed_at >= observed_from)
    if observed_to is not None:
        statement = statement.where(ExchangeRateModel.observed_at <= observed_to)
    statement = (
        statement.order_by(ExchangeRateModel.observed_at.desc(), ExchangeRateModel.id.desc())
        .limit(limit)
        .offset(offset)
    )
    return list(await session.scalars(statement))


async def resolve_exchange_rate(
    session: AsyncSession,
    *,
    base_currency: str,
    quote_currency: str,
    valuation_as_of: datetime,
    stale_after_seconds: int,
) -> ResolvedFxRate:
    currencies = {base_currency, quote_currency, "RUB"}
    records = list(
        await session.scalars(
            select(ExchangeRateModel)
            .where(
                ExchangeRateModel.observed_at <= valuation_as_of,
                ExchangeRateModel.base_currency.in_(currencies),
                ExchangeRateModel.quote_currency.in_(currencies),
            )
            .order_by(ExchangeRateModel.observed_at, ExchangeRateModel.id)
        )
    )
    return resolve_fx_rate(
        [_candidate(record) for record in records],
        base_currency=base_currency,
        quote_currency=quote_currency,
        valuation_as_of=valuation_as_of,
        stale_after_seconds=stale_after_seconds,
    )


async def save_provider_observation(
    session: AsyncSession,
    observation: FxRateObservation,
) -> tuple[ExchangeRateModel, bool]:
    identity = (
        ExchangeRateModel.provider == observation.provider,
        ExchangeRateModel.base_currency == observation.base_currency,
        ExchangeRateModel.quote_currency == observation.quote_currency,
        ExchangeRateModel.observed_at == observation.observed_at,
    )
    existing = await session.scalar(select(ExchangeRateModel).where(*identity))
    if existing is not None:
        return existing, True
    record = ExchangeRateModel(
        base_currency=observation.base_currency,
        quote_currency=observation.quote_currency,
        rate=observation.rate,
        observed_at=observation.observed_at,
        fetched_at=observation.fetched_at,
        provider=observation.provider,
        source=observation.source,
        mode=observation.mode,
    )
    session.add(record)
    try:
        await session.commit()
    except IntegrityError:
        await session.rollback()
        raced = await session.scalar(select(ExchangeRateModel).where(*identity))
        if raced is None:
            raise
        return raced, True
    await session.refresh(record)
    return record, False


async def synchronize_usd_rub(
    session: AsyncSession,
    provider: FxRateProvider,
) -> tuple[ExchangeRateModel, bool]:
    observation = await provider.fetch_rate(base_currency="USD", quote_currency="RUB")
    return await save_provider_observation(session, observation)


async def enqueue_fx_sync(
    session: AsyncSession,
    *,
    minimum_interval_seconds: int,
) -> tuple[ImportJobModel, bool]:
    # Serialize enqueue from API and concurrent workers; observation identity alone
    # would not prevent duplicate external requests.
    await session.execute(select(func.pg_advisory_xact_lock(47102, 1)))
    threshold = datetime.now(UTC) - timedelta(seconds=minimum_interval_seconds)
    existing = await session.scalar(
        select(ImportJobModel)
        .where(
            ImportJobModel.job_type == ImportJobType.FX_SYNC,
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
        job_type=ImportJobType.FX_SYNC,
        status=ImportJobStatus.PENDING,
        payload={"pairs": ["USD/RUB"]},
    )
    session.add(job)
    await session.commit()
    await session.refresh(job)
    return job, False


async def get_latest_fx_sync_job(session: AsyncSession) -> ImportJobModel | None:
    result = await session.scalars(
        select(ImportJobModel)
        .where(ImportJobModel.job_type == ImportJobType.FX_SYNC)
        .order_by(ImportJobModel.created_at.desc(), ImportJobModel.id.desc())
        .limit(1)
    )
    return result.one_or_none()


async def get_fx_sync_job(session: AsyncSession, job_id: UUID) -> ImportJobModel | None:
    statement: Select[tuple[ImportJobModel]] = select(ImportJobModel).where(
        ImportJobModel.id == job_id,
        ImportJobModel.job_type == ImportJobType.FX_SYNC,
    )
    result = await session.scalars(statement)
    return result.one_or_none()


def build_fx_rate_provider(settings: Settings) -> FxRateProvider:
    return CbrDailyFxRateProvider(
        timeout_seconds=settings.fx_http_timeout_seconds,
        max_attempts=settings.fx_max_attempts,
        retry_backoff_seconds=settings.fx_retry_backoff_seconds,
        max_response_bytes=settings.fx_response_max_bytes,
    )


def _candidate(record: ExchangeRateModel) -> FxRateCandidate:
    return FxRateCandidate(
        id=record.id,
        base_currency=record.base_currency,
        quote_currency=record.quote_currency,
        rate=record.rate,
        observed_at=record.observed_at,
        provider=record.provider,
        source=record.source,
        mode=record.mode,
    )
