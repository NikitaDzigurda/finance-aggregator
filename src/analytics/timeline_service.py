from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from datetime import UTC, date, datetime, time, timedelta
from decimal import ROUND_HALF_EVEN, Context, Decimal, localcontext
from enum import StrEnum
from uuid import UUID
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from sqlalchemy.ext.asyncio import AsyncSession

from accounts.models import AccountType
from accounts.service import list_portfolio_accounts
from analytics.schemas import (
    AnalyticsDiagnosticResponse,
    EventTimelineResponse,
    TimelineBucket,
    TimelineBucketValueResponse,
    TimelineMetric,
    TimelineProvenanceResponse,
    TimelineSeriesResponse,
    TimelineUnitType,
)
from calculation.contracts import (
    CALCULATION_CONTRACT_VERSION,
    OPERATION_EFFECTS_CONTRACT_VERSION,
    RealisedPnlEffectStatus,
)
from calculation.service import derive_operation_effects, get_position_snapshot
from operations.schemas import (
    AssetFeePayload,
    CashMovementDirection,
    CashMovementPayload,
    CryptoTradePayload,
    FeePayload,
    IncomePayload,
    TaxPayload,
    TradePayload,
    operation_response,
)
from operations.service import (
    count_portfolio_operations,
    list_portfolio_operations_before,
)
from portfolios.service import get_portfolio
from shared.errors import ApiErrorException
from shared.exact import MONEY_SPEC, validate_decimal

_TIMELINE_CONTEXT = Context(prec=100, rounding=ROUND_HALF_EVEN)
_MONEY_QUANTUM = Decimal("0.000000000000000001")
MAX_TIMELINE_RANGE_DAYS = 366
MAX_TIMELINE_SERIES_PAGE_SIZE = 100


class TimelineView(StrEnum):
    CASH_FLOWS = "cash_flows"
    INCOME = "income"
    COSTS = "costs"
    TRADING = "trading"


@dataclass(frozen=True, slots=True)
class _TimelineValue:
    metric: TimelineMetric
    unit_type: TimelineUnitType
    unit: str
    bucket_start: date
    value: Decimal


async def build_event_timeline(
    session: AsyncSession,
    *,
    portfolio_id: UUID,
    period_from: date,
    period_to: date,
    bucket: TimelineBucket,
    timezone_name: str,
    view: TimelineView,
    unit_type: TimelineUnitType | None = None,
    unit: str | None = None,
    limit: int = MAX_TIMELINE_SERIES_PAGE_SIZE,
    offset: int = 0,
) -> EventTimelineResponse:
    if period_from >= period_to:
        raise ApiErrorException(
            status_code=422,
            code="analytics_period_invalid",
            message="The 'from' date must be earlier than the 'to' date",
        )
    if (period_to - period_from).days > MAX_TIMELINE_RANGE_DAYS:
        raise ApiErrorException(
            status_code=422,
            code="analytics_period_too_large",
            message=f"The requested period must not exceed {MAX_TIMELINE_RANGE_DAYS} days",
        )
    timezone = _timezone(timezone_name)
    portfolio = await get_portfolio(session, portfolio_id)
    if portfolio is None:
        raise ApiErrorException(
            status_code=404,
            code="portfolio_not_found",
            message="Portfolio was not found",
        )

    period_start_utc = datetime.combine(period_from, time.min, timezone).astimezone(UTC)
    period_end_utc = datetime.combine(period_to, time.min, timezone).astimezone(UTC)
    accounts = await list_portfolio_accounts(session, portfolio_id=portfolio_id)
    account_ids = {
        account.id
        for account in accounts
        if account.account_type in {AccountType.BROKER, AccountType.CEX}
    }
    records = await list_portfolio_operations_before(
        session,
        portfolio_id=portfolio_id,
        occurred_before=period_end_utc,
        account_ids=account_ids,
    )
    effects = derive_operation_effects(records)
    effect_by_operation = {item.operation_id: item for item in effects.effects}
    values: list[_TimelineValue] = []
    diagnostics = [
        _diagnostic(
            "warning",
            "historical_reporting_conversion_unavailable",
            "Historical events remain separated by original currency or asset unit",
        )
    ]
    for record in records:
        if record.occurred_at < period_start_utc:
            continue
        operation = operation_response(record)
        bucket_start = _bucket_start(record.occurred_at.astimezone(timezone).date(), bucket)
        payload = operation.payload
        if view is TimelineView.CASH_FLOWS and isinstance(payload, CashMovementPayload):
            metric = (
                TimelineMetric.DEPOSITS
                if payload.direction is CashMovementDirection.DEPOSIT
                else TimelineMetric.WITHDRAWALS
            )
            values.append(
                _TimelineValue(
                    metric,
                    TimelineUnitType.CURRENCY,
                    payload.currency,
                    bucket_start,
                    payload.amount,
                )
            )
        elif view is TimelineView.INCOME and isinstance(payload, IncomePayload):
            values.append(
                _TimelineValue(
                    TimelineMetric.INCOME,
                    TimelineUnitType.CURRENCY,
                    payload.currency,
                    bucket_start,
                    payload.amount,
                )
            )
        elif view is TimelineView.COSTS:
            if isinstance(payload, FeePayload):
                values.append(
                    _TimelineValue(
                        TimelineMetric.FEES,
                        TimelineUnitType.CURRENCY,
                        payload.currency,
                        bucket_start,
                        payload.amount,
                    )
                )
            elif isinstance(payload, AssetFeePayload):
                values.append(
                    _TimelineValue(
                        TimelineMetric.FEES,
                        TimelineUnitType.ASSET,
                        str(payload.instrument_id),
                        bucket_start,
                        payload.quantity,
                    )
                )
            elif isinstance(payload, TaxPayload):
                values.append(
                    _TimelineValue(
                        TimelineMetric.TAXES,
                        TimelineUnitType.CURRENCY,
                        payload.currency,
                        bucket_start,
                        payload.amount,
                    )
                )
        elif view is TimelineView.TRADING:
            if isinstance(payload, TradePayload):
                values.append(
                    _TimelineValue(
                        TimelineMetric.TRADE_TURNOVER,
                        TimelineUnitType.CURRENCY,
                        payload.price_currency,
                        bucket_start,
                        _money(payload.quantity * payload.price),
                    )
                )
            elif isinstance(payload, CryptoTradePayload):
                values.extend(
                    (
                        _TimelineValue(
                            TimelineMetric.TRADE_TURNOVER,
                            TimelineUnitType.ASSET,
                            str(payload.sold_instrument_id),
                            bucket_start,
                            payload.sold_quantity,
                        ),
                        _TimelineValue(
                            TimelineMetric.TRADE_TURNOVER,
                            TimelineUnitType.ASSET,
                            str(payload.bought_instrument_id),
                            bucket_start,
                            payload.bought_quantity,
                        ),
                    )
                )
            effect = effect_by_operation[record.id]
            for realised in effect.realised_pnl:
                values.append(
                    _TimelineValue(
                        TimelineMetric.REALISED_PNL,
                        TimelineUnitType.CURRENCY,
                        realised.currency,
                        bucket_start,
                        realised.amount,
                    )
                )
            if effect.realised_pnl_status is RealisedPnlEffectStatus.UNAVAILABLE:
                diagnostics.append(
                    _diagnostic(
                        "warning",
                        "realised_pnl_unavailable",
                        "At least one disposal has no deterministic realised P&L",
                    )
                )

    snapshot = await get_position_snapshot(session, portfolio_id)
    ledger_count = await count_portfolio_operations(session, portfolio_id=portfolio_id)
    snapshot_fresh = (
        ledger_count == 0 if snapshot is None else ledger_count == snapshot.operation_count
    )
    all_series = _series(values)
    filtered_series = [
        item
        for item in all_series
        if (unit_type is None or item.unit_type is unit_type)
        and (unit is None or item.unit == unit)
    ]
    return EventTimelineResponse(
        portfolio_id=portfolio.id,
        period_from=period_from,
        period_to=period_to,
        bucket=bucket,
        timezone=timezone_name,
        series_total=len(filtered_series),
        limit=limit,
        offset=offset,
        series=filtered_series[offset : offset + limit],
        diagnostics=_deduplicate_diagnostics(diagnostics),
        provenance=TimelineProvenanceResponse(
            calculation_contract_version=CALCULATION_CONTRACT_VERSION,
            operation_effects_contract_version=OPERATION_EFFECTS_CONTRACT_VERSION,
            calculation_as_of=period_end_utc,
            snapshot_as_of=snapshot.as_of if snapshot is not None else None,
            snapshot_fresh=snapshot_fresh,
        ),
    )


def _timezone(value: str) -> ZoneInfo:
    try:
        timezone = ZoneInfo(value)
    except (ZoneInfoNotFoundError, ValueError) as exc:
        raise ApiErrorException(
            status_code=422,
            code="analytics_timezone_invalid",
            message="Timezone must be a valid IANA timezone name",
        ) from exc
    if value != timezone.key:
        raise ApiErrorException(
            status_code=422,
            code="analytics_timezone_invalid",
            message="Timezone must be a valid IANA timezone name",
        )
    return timezone


def _bucket_start(value: date, bucket: TimelineBucket) -> date:
    if bucket is TimelineBucket.DAY:
        return value
    if bucket is TimelineBucket.WEEK:
        return value - timedelta(days=value.weekday())
    return value.replace(day=1)


def _series(values: list[_TimelineValue]) -> list[TimelineSeriesResponse]:
    grouped: defaultdict[tuple[TimelineMetric, TimelineUnitType, str, date], Decimal]
    grouped = defaultdict(Decimal)
    for item in values:
        key = (item.metric, item.unit_type, item.unit, item.bucket_start)
        grouped[key] = _exact(grouped[key] + item.value)
    by_series: defaultdict[
        tuple[TimelineMetric, TimelineUnitType, str],
        list[TimelineBucketValueResponse],
    ] = defaultdict(list)
    for (metric, unit_type, unit, bucket_start), value in sorted(
        grouped.items(),
        key=lambda item: (
            item[0][0].value,
            item[0][1].value,
            item[0][2],
            item[0][3],
        ),
    ):
        by_series[(metric, unit_type, unit)].append(
            TimelineBucketValueResponse(bucket_start=bucket_start, value=value)
        )
    return [
        TimelineSeriesResponse(
            key=f"{metric.value}:{unit_type.value}:{unit}",
            metric=metric,
            unit_type=unit_type,
            unit=unit,
            buckets=buckets,
        )
        for (metric, unit_type, unit), buckets in sorted(
            by_series.items(),
            key=lambda item: (item[0][0].value, item[0][1].value, item[0][2]),
        )
    ]


def _money(value: Decimal) -> Decimal:
    with localcontext(_TIMELINE_CONTEXT):
        return validate_decimal(
            value.quantize(_MONEY_QUANTUM, rounding=ROUND_HALF_EVEN),
            MONEY_SPEC,
        )


def _exact(value: Decimal) -> Decimal:
    return validate_decimal(value, MONEY_SPEC)


def _diagnostic(
    severity: str,
    code: str,
    message: str,
) -> AnalyticsDiagnosticResponse:
    return AnalyticsDiagnosticResponse.model_validate(
        {"severity": severity, "code": code, "message": message}
    )


def _deduplicate_diagnostics(
    values: list[AnalyticsDiagnosticResponse],
) -> list[AnalyticsDiagnosticResponse]:
    unique = {(item.severity, item.code): item for item in values}
    return [unique[key] for key in sorted(unique)]
