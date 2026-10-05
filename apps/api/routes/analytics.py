from __future__ import annotations

from datetime import date
from typing import Annotated, Any
from uuid import UUID

from fastapi import APIRouter, Depends, Query, status
from sqlalchemy.ext.asyncio import AsyncSession

from analytics.contracts import ReportingCurrency
from analytics.quality_service import build_data_quality_response
from analytics.schemas import (
    ActualAllocationResponse,
    AnalyticsBreakdownResponse,
    AnalyticsDataQualityResponse,
    AnalyticsHoldingsResponse,
    AnalyticsOverviewResponse,
    CounterpartyExposureResponse,
    EventTimelineResponse,
    TimelineBucket,
    TimelineUnitType,
)
from analytics.service import (
    AnalyticsReadModel,
    allocation_response,
    breakdown_response,
    build_analytics_read_model,
    exposure_response,
    holdings_response,
    overview_response,
)
from analytics.timeline_service import (
    MAX_TIMELINE_RANGE_DAYS,
    MAX_TIMELINE_SERIES_PAGE_SIZE,
    TimelineView,
    build_event_timeline,
)
from shared.config import Settings, get_settings
from shared.database import get_db_session
from shared.errors import ErrorResponse

router = APIRouter(prefix="/api/v1/portfolios", tags=["analytics"])

ERROR_RESPONSES: dict[int | str, dict[str, Any]] = {
    status.HTTP_404_NOT_FOUND: {
        "model": ErrorResponse,
        "description": "Portfolio was not found",
    }
}


def _overview_metric_example(
    metric: str,
    *,
    quality: str,
    total_value: str | None,
    known_value: str,
    included_components: int,
    excluded_components: int,
) -> dict[str, object]:
    return {
        "metric": metric,
        "valuation_as_of": "2026-08-22T12:00:00Z",
        "reporting_currency": "RUB",
        "quality": quality,
        "total_value": total_value,
        "known_value": known_value,
        "included_components": included_components,
        "excluded_components": excluded_components,
        "diagnostics": (
            []
            if excluded_components == 0
            else [
                {
                    "severity": "warning",
                    "code": "metric_input_unavailable",
                    "message": "A dependent metric component is unavailable",
                }
            ]
        ),
    }


def _overview_example(
    *,
    quality: str,
    total_value: str | None,
    known_value: str,
    included_components: int,
    excluded_components: int,
    diagnostics: list[dict[str, str]],
) -> dict[str, object]:
    independent_metrics = {
        metric: _overview_metric_example(
            metric,
            quality="complete",
            total_value="0.000000000000000000",
            known_value="0.000000000000000000",
            included_components=0,
            excluded_components=0,
        )
        for metric in (
            "cost_basis",
            "realised_pnl",
            "unrealised_pnl",
            "income",
            "fees",
            "taxes",
        )
    }
    return {
        "portfolio_id": "00000000-0000-4000-8000-000000000001",
        "valuation_as_of": "2026-08-22T12:00:00Z",
        "reporting_currency": "RUB",
        "snapshot_as_of": "2026-08-22T12:00:00Z",
        "snapshot_fresh": True,
        "ledger_operation_count": included_components + excluded_components,
        "diagnostics": diagnostics,
        "current_value": _overview_metric_example(
            "current_value",
            quality=quality,
            total_value=total_value,
            known_value=known_value,
            included_components=included_components,
            excluded_components=excluded_components,
        ),
        **independent_metrics,
    }


OVERVIEW_EXAMPLES = {
    "complete": {
        "summary": "Complete synthetic portfolio",
        "value": _overview_example(
            quality="complete",
            total_value="125000.000000000000000000",
            known_value="125000.000000000000000000",
            included_components=3,
            excluded_components=0,
            diagnostics=[],
        ),
    },
    "partial": {
        "summary": "Partial synthetic portfolio with a missing price",
        "value": _overview_example(
            quality="partial",
            total_value=None,
            known_value="80000.000000000000000000",
            included_components=2,
            excluded_components=1,
            diagnostics=[
                {
                    "severity": "warning",
                    "code": "market_price_missing",
                    "message": "A required market price is unavailable",
                }
            ],
        ),
    },
    "empty": {
        "summary": "Empty synthetic portfolio",
        "value": _overview_example(
            quality="complete",
            total_value="0.000000000000000000",
            known_value="0.000000000000000000",
            included_components=0,
            excluded_components=0,
            diagnostics=[],
        ),
    },
}

OVERVIEW_RESPONSES = {
    **ERROR_RESPONSES,
    status.HTTP_200_OK: {
        "description": "Current analytics for a complete, partial, or empty portfolio",
        "content": {"application/json": {"examples": OVERVIEW_EXAMPLES}},
    },
}

TIMELINE_CONTRACT_DESCRIPTION = (
    f" Periods are limited to {MAX_TIMELINE_RANGE_DAYS} days. Optional unit_type and "
    "unit filters are exact. Series are ordered by metric, unit type, and unit; buckets "
    "are ascending. Limit/offset paginate series, not buckets."
)


async def _read_model(
    portfolio_id: UUID,
    reporting_currency: ReportingCurrency | None,
    session: AsyncSession,
    settings: Settings,
) -> AnalyticsReadModel:
    return await build_analytics_read_model(
        session,
        portfolio_id=portfolio_id,
        reporting_currency=reporting_currency,
        settings=settings,
    )


@router.get(
    "/{portfolio_id}/analytics/overview",
    response_model=AnalyticsOverviewResponse,
    responses=OVERVIEW_RESPONSES,
    summary="Get the current portfolio analytics overview",
    description=(
        "Aggregates the persisted calculation snapshot into RUB or USD without changing "
        "Ledger, cost basis, prices, or FX observations. Missing data remains explicit."
    ),
)
async def overview_route(
    portfolio_id: UUID,
    session: Annotated[AsyncSession, Depends(get_db_session)],
    settings: Annotated[Settings, Depends(get_settings)],
    reporting_currency: Annotated[ReportingCurrency | None, Query()] = None,
) -> AnalyticsOverviewResponse:
    model = await _read_model(portfolio_id, reporting_currency, session, settings)
    return overview_response(model)


@router.get(
    "/{portfolio_id}/analytics/holdings",
    response_model=AnalyticsHoldingsResponse,
    responses=ERROR_RESPONSES,
    summary="Get current instrument and cash holdings",
    description=(
        "Returns broker and CEX holdings with account, institution, effective allocation "
        "category, exact reporting values, P&L, and local diagnostics."
    ),
)
async def holdings_route(
    portfolio_id: UUID,
    session: Annotated[AsyncSession, Depends(get_db_session)],
    settings: Annotated[Settings, Depends(get_settings)],
    reporting_currency: Annotated[ReportingCurrency | None, Query()] = None,
) -> AnalyticsHoldingsResponse:
    model = await _read_model(portfolio_id, reporting_currency, session, settings)
    return holdings_response(model)


@router.get(
    "/{portfolio_id}/analytics/breakdown",
    response_model=AnalyticsBreakdownResponse,
    responses=ERROR_RESPONSES,
    summary="Get current portfolio breakdowns",
    description=(
        "Returns account, institution, source currency, instrument type, and allocation "
        "category projections over the same explicit known-value denominator."
    ),
)
async def breakdown_route(
    portfolio_id: UUID,
    session: Annotated[AsyncSession, Depends(get_db_session)],
    settings: Annotated[Settings, Depends(get_settings)],
    reporting_currency: Annotated[ReportingCurrency | None, Query()] = None,
) -> AnalyticsBreakdownResponse:
    model = await _read_model(portfolio_id, reporting_currency, session, settings)
    return breakdown_response(model)


@router.get(
    "/{portfolio_id}/analytics/exposure",
    response_model=CounterpartyExposureResponse,
    responses=ERROR_RESPONSES,
    summary="Get broker and CEX counterparty exposure",
    description=(
        "Aggregates known current value by broker or CEX institution. It does not score "
        "risk, recommend limits, or include legacy bank accounts."
    ),
)
async def exposure_route(
    portfolio_id: UUID,
    session: Annotated[AsyncSession, Depends(get_db_session)],
    settings: Annotated[Settings, Depends(get_settings)],
    reporting_currency: Annotated[ReportingCurrency | None, Query()] = None,
) -> CounterpartyExposureResponse:
    model = await _read_model(portfolio_id, reporting_currency, session, settings)
    return exposure_response(model)


@router.get(
    "/{portfolio_id}/allocation",
    response_model=ActualAllocationResponse,
    responses=ERROR_RESPONSES,
    summary="Get actual portfolio allocation",
    description=(
        "Returns current known value and actual weight by allocation category. "
        "The denominator, partial coverage, and unavailable components remain explicit."
    ),
)
async def allocation_route(
    portfolio_id: UUID,
    session: Annotated[AsyncSession, Depends(get_db_session)],
    settings: Annotated[Settings, Depends(get_settings)],
    reporting_currency: Annotated[ReportingCurrency | None, Query()] = None,
) -> ActualAllocationResponse:
    model = await _read_model(portfolio_id, reporting_currency, session, settings)
    return allocation_response(model)


@router.get(
    "/{portfolio_id}/analytics/data-quality",
    response_model=AnalyticsDataQualityResponse,
    responses=ERROR_RESPONSES,
    summary="Get safe portfolio data-quality diagnostics",
    description=(
        "Aggregates import completeness, reconciliation, unresolved review, calculation, "
        "price, and FX diagnostics into stable counts and metric impacts. It never returns "
        "raw import rows, account or instrument identifiers, addresses, or financial values."
    ),
)
async def data_quality_route(
    portfolio_id: UUID,
    session: Annotated[AsyncSession, Depends(get_db_session)],
    settings: Annotated[Settings, Depends(get_settings)],
    reporting_currency: Annotated[ReportingCurrency | None, Query()] = None,
) -> AnalyticsDataQualityResponse:
    return await build_data_quality_response(
        session,
        portfolio_id=portfolio_id,
        reporting_currency=reporting_currency,
        settings=settings,
    )


async def _timeline(
    portfolio_id: UUID,
    period_from: date,
    period_to: date,
    bucket: TimelineBucket,
    timezone: str,
    view: TimelineView,
    unit_type: TimelineUnitType | None,
    unit: str | None,
    limit: int,
    offset: int,
    session: AsyncSession,
) -> EventTimelineResponse:
    return await build_event_timeline(
        session,
        portfolio_id=portfolio_id,
        period_from=period_from,
        period_to=period_to,
        bucket=bucket,
        timezone_name=timezone,
        view=view,
        unit_type=unit_type,
        unit=unit,
        limit=limit,
        offset=offset,
    )


@router.get(
    "/{portfolio_id}/analytics/cash-flows",
    response_model=EventTimelineResponse,
    responses=ERROR_RESPONSES,
    summary="Get Ledger cash-flow event timeline",
    description=(
        "Buckets deposits and withdrawals by user IANA timezone. Values remain in their "
        "original currencies and are never converted with a current FX rate. The 'to' "
        "date is exclusive."
        + TIMELINE_CONTRACT_DESCRIPTION
    ),
)
async def cash_flows_route(
    portfolio_id: UUID,
    session: Annotated[AsyncSession, Depends(get_db_session)],
    period_from: Annotated[
        date,
        Query(alias="from", description="Inclusive local calendar date"),
    ],
    period_to: Annotated[
        date,
        Query(
            alias="to",
            description=(
                "Exclusive local calendar date; the period may not exceed "
                f"{MAX_TIMELINE_RANGE_DAYS} days"
            ),
        ),
    ],
    bucket: Annotated[TimelineBucket, Query(description="Calendar bucket size")],
    timezone: Annotated[
        str,
        Query(
            min_length=1,
            max_length=128,
            description="Canonical IANA timezone used for period boundaries and buckets",
        ),
    ],
    unit_type: Annotated[
        TimelineUnitType | None,
        Query(description="Optional exact currency/asset series filter"),
    ] = None,
    unit: Annotated[
        str | None,
        Query(
            min_length=1,
            max_length=64,
            description="Optional exact currency code or asset UUID series filter",
        ),
    ] = None,
    limit: Annotated[
        int,
        Query(ge=1, le=MAX_TIMELINE_SERIES_PAGE_SIZE, description="Series page size"),
    ] = MAX_TIMELINE_SERIES_PAGE_SIZE,
    offset: Annotated[
        int,
        Query(ge=0, description="Zero-based offset in the stable series ordering"),
    ] = 0,
) -> EventTimelineResponse:
    return await _timeline(
        portfolio_id,
        period_from,
        period_to,
        bucket,
        timezone,
        TimelineView.CASH_FLOWS,
        unit_type,
        unit,
        limit,
        offset,
        session,
    )


@router.get(
    "/{portfolio_id}/analytics/income",
    response_model=EventTimelineResponse,
    responses=ERROR_RESPONSES,
    summary="Get Ledger income event timeline",
    description=(
        "Buckets dividend, coupon, interest, and other income events in original "
        "currencies without historical value reconstruction. The 'to' date is exclusive."
        + TIMELINE_CONTRACT_DESCRIPTION
    ),
)
async def income_route(
    portfolio_id: UUID,
    session: Annotated[AsyncSession, Depends(get_db_session)],
    period_from: Annotated[date, Query(alias="from", description="Inclusive local date")],
    period_to: Annotated[
        date,
        Query(
            alias="to",
            description=(
                "Exclusive local date; the period may not exceed "
                f"{MAX_TIMELINE_RANGE_DAYS} days"
            ),
        ),
    ],
    bucket: Annotated[TimelineBucket, Query(description="Calendar bucket size")],
    timezone: Annotated[
        str,
        Query(min_length=1, max_length=128, description="Canonical IANA timezone"),
    ],
    unit_type: Annotated[
        TimelineUnitType | None,
        Query(description="Optional exact currency/asset series filter"),
    ] = None,
    unit: Annotated[
        str | None,
        Query(
            min_length=1,
            max_length=64,
            description="Optional exact currency code or asset UUID series filter",
        ),
    ] = None,
    limit: Annotated[
        int,
        Query(ge=1, le=MAX_TIMELINE_SERIES_PAGE_SIZE, description="Series page size"),
    ] = (
        MAX_TIMELINE_SERIES_PAGE_SIZE
    ),
    offset: Annotated[
        int,
        Query(ge=0, description="Zero-based offset in the stable series ordering"),
    ] = 0,
) -> EventTimelineResponse:
    return await _timeline(
        portfolio_id,
        period_from,
        period_to,
        bucket,
        timezone,
        TimelineView.INCOME,
        unit_type,
        unit,
        limit,
        offset,
        session,
    )


@router.get(
    "/{portfolio_id}/analytics/costs",
    response_model=EventTimelineResponse,
    responses=ERROR_RESPONSES,
    summary="Get Ledger fee and tax event timeline",
    description=(
        "Buckets fees and taxes independently; asset fees remain asset-unit series. "
        "No total-result metric duplicates these costs. The 'to' date is exclusive."
        + TIMELINE_CONTRACT_DESCRIPTION
    ),
)
async def costs_route(
    portfolio_id: UUID,
    session: Annotated[AsyncSession, Depends(get_db_session)],
    period_from: Annotated[date, Query(alias="from", description="Inclusive local date")],
    period_to: Annotated[
        date,
        Query(
            alias="to",
            description=(
                "Exclusive local date; the period may not exceed "
                f"{MAX_TIMELINE_RANGE_DAYS} days"
            ),
        ),
    ],
    bucket: Annotated[TimelineBucket, Query(description="Calendar bucket size")],
    timezone: Annotated[
        str,
        Query(min_length=1, max_length=128, description="Canonical IANA timezone"),
    ],
    unit_type: Annotated[
        TimelineUnitType | None,
        Query(description="Optional exact currency/asset series filter"),
    ] = None,
    unit: Annotated[
        str | None,
        Query(
            min_length=1,
            max_length=64,
            description="Optional exact currency code or asset UUID series filter",
        ),
    ] = None,
    limit: Annotated[
        int,
        Query(ge=1, le=MAX_TIMELINE_SERIES_PAGE_SIZE, description="Series page size"),
    ] = (
        MAX_TIMELINE_SERIES_PAGE_SIZE
    ),
    offset: Annotated[
        int,
        Query(ge=0, description="Zero-based offset in the stable series ordering"),
    ] = 0,
) -> EventTimelineResponse:
    return await _timeline(
        portfolio_id,
        period_from,
        period_to,
        bucket,
        timezone,
        TimelineView.COSTS,
        unit_type,
        unit,
        limit,
        offset,
        session,
    )


@router.get(
    "/{portfolio_id}/analytics/trading",
    response_model=EventTimelineResponse,
    responses=ERROR_RESPONSES,
    summary="Get Ledger trading event timeline",
    description=(
        "Buckets exact trade turnover and deterministic available realised P&L. Crypto "
        "trades use separate asset-unit turnover legs; transfers are excluded. The 'to' "
        "date is exclusive."
        + TIMELINE_CONTRACT_DESCRIPTION
    ),
)
async def trading_route(
    portfolio_id: UUID,
    session: Annotated[AsyncSession, Depends(get_db_session)],
    period_from: Annotated[date, Query(alias="from", description="Inclusive local date")],
    period_to: Annotated[
        date,
        Query(
            alias="to",
            description=(
                "Exclusive local date; the period may not exceed "
                f"{MAX_TIMELINE_RANGE_DAYS} days"
            ),
        ),
    ],
    bucket: Annotated[TimelineBucket, Query(description="Calendar bucket size")],
    timezone: Annotated[
        str,
        Query(min_length=1, max_length=128, description="Canonical IANA timezone"),
    ],
    unit_type: Annotated[
        TimelineUnitType | None,
        Query(description="Optional exact currency/asset series filter"),
    ] = None,
    unit: Annotated[
        str | None,
        Query(
            min_length=1,
            max_length=64,
            description="Optional exact currency code or asset UUID series filter",
        ),
    ] = None,
    limit: Annotated[
        int,
        Query(ge=1, le=MAX_TIMELINE_SERIES_PAGE_SIZE, description="Series page size"),
    ] = (
        MAX_TIMELINE_SERIES_PAGE_SIZE
    ),
    offset: Annotated[
        int,
        Query(ge=0, description="Zero-based offset in the stable series ordering"),
    ] = 0,
) -> EventTimelineResponse:
    return await _timeline(
        portfolio_id,
        period_from,
        period_to,
        bucket,
        timezone,
        TimelineView.TRADING,
        unit_type,
        unit,
        limit,
        offset,
        session,
    )
