from __future__ import annotations

from datetime import UTC, datetime
from typing import Annotated, Any
from uuid import UUID

from fastapi import APIRouter, Body, Depends, Query, status
from sqlalchemy.ext.asyncio import AsyncSession

from apps.api.routes.common import commit_or_conflict, not_found
from pricing.fx import ResolvedFxRate
from pricing.schemas import (
    ExchangeRateBatchCreate,
    ExchangeRateBatchResponse,
    ExchangeRateListResponse,
    ExchangeRateResponse,
    FxRateDiagnosticResponse,
    FxRateResolveResponse,
    FxRateUseResponse,
    FxSyncJobResponse,
)
from pricing.service import (
    create_manual_exchange_rates,
    enqueue_fx_sync,
    get_fx_sync_job,
    get_latest_fx_sync_job,
    list_exchange_rates,
    resolve_exchange_rate,
)
from shared.config import get_settings
from shared.database import get_db_session
from shared.errors import ApiErrorException, ErrorResponse
from shared.exact import AwareDateTime, CurrencyCode

router = APIRouter(prefix="/api/v1/fx-rates", tags=["pricing"])

NOT_FOUND_RESPONSE: dict[int | str, dict[str, Any]] = {
    status.HTTP_404_NOT_FOUND: {
        "model": ErrorResponse,
        "description": "FX synchronization job was not found",
    }
}
EXCHANGE_RATE_BATCH_EXAMPLES = {
    "manual_fallback": {
        "summary": "Atomic manual USD/RUB fallback",
        "value": {
            "items": [
                {
                    "base_currency": "USD",
                    "quote_currency": "RUB",
                    "rate": "80.125",
                    "observed_at": "2026-08-21T00:00:00+03:00",
                }
            ]
        },
    }
}


@router.post(
    "/batch",
    response_model=ExchangeRateBatchResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Record an atomic batch of manual FX rates",
    description=(
        "Stores exact append-only fallback rates in one transaction. Rates use the convention "
        "one unit of base_currency equals rate units of quote_currency. A rejected item leaves "
        "the entire batch unchanged."
    ),
)
async def create_exchange_rate_batch_route(
    payload: Annotated[
        ExchangeRateBatchCreate,
        Body(openapi_examples=EXCHANGE_RATE_BATCH_EXAMPLES),
    ],
    session: Annotated[AsyncSession, Depends(get_db_session)],
) -> ExchangeRateBatchResponse:
    records = create_manual_exchange_rates(payload.items)
    session.add_all(records)
    await commit_or_conflict(
        session,
        code="exchange_rate_batch_conflict",
        message="Exchange-rate batch conflicts with existing pricing data",
    )
    for record in records:
        await session.refresh(record)
    return ExchangeRateBatchResponse(
        items=[ExchangeRateResponse.model_validate(item) for item in records]
    )


@router.get(
    "",
    response_model=ExchangeRateListResponse,
    summary="List exact exchange-rate observations",
    description=(
        "Lists append-only automatic and manual observations, newest first. Filters use the "
        "stored base/quote orientation and inclusive observation times."
    ),
)
async def list_exchange_rates_route(
    session: Annotated[AsyncSession, Depends(get_db_session)],
    base_currency: CurrencyCode | None = None,
    quote_currency: CurrencyCode | None = None,
    observed_from: AwareDateTime | None = None,
    observed_to: AwareDateTime | None = None,
    limit: Annotated[int, Query(ge=1, le=200)] = 100,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> ExchangeRateListResponse:
    if (
        observed_from is not None
        and observed_to is not None
        and observed_from > observed_to
    ):
        raise ApiErrorException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            code="exchange_rate_period_invalid",
            message="observed_from must not be later than observed_to",
        )
    records = await list_exchange_rates(
        session,
        base_currency=base_currency,
        quote_currency=quote_currency,
        observed_from=observed_from,
        observed_to=observed_to,
        limit=limit,
        offset=offset,
    )
    return ExchangeRateListResponse(
        items=[ExchangeRateResponse.model_validate(item) for item in records],
        limit=limit,
        offset=offset,
    )


@router.get(
    "/resolve",
    response_model=FxRateResolveResponse,
    summary="Resolve an FX rate at a valuation instant",
    description=(
        "Selects only observations at or before valuation_as_of. Resolution is limited to "
        "identity, direct, inverse, or one RUB pivot; it never searches an arbitrary graph or "
        "substitutes one for a missing cross-currency rate."
    ),
)
async def resolve_exchange_rate_route(
    session: Annotated[AsyncSession, Depends(get_db_session)],
    base_currency: CurrencyCode,
    quote_currency: CurrencyCode,
    valuation_as_of: AwareDateTime | None = None,
) -> FxRateResolveResponse:
    settings = get_settings()
    resolved = await resolve_exchange_rate(
        session,
        base_currency=base_currency,
        quote_currency=quote_currency,
        valuation_as_of=valuation_as_of or datetime.now(UTC),
        stale_after_seconds=settings.fx_stale_after_seconds,
    )
    return _resolved_response(resolved)


@router.post(
    "/sync",
    response_model=FxSyncJobResponse,
    status_code=status.HTTP_202_ACCEPTED,
    summary="Queue a public USD/RUB FX synchronization",
    description=(
        "Queues a rate-limited PostgreSQL worker job. This request performs no external network "
        "I/O and sends no portfolio, account, import, or user data to the provider."
    ),
)
async def enqueue_fx_sync_route(
    session: Annotated[AsyncSession, Depends(get_db_session)],
) -> FxSyncJobResponse:
    settings = get_settings()
    job, duplicate = await enqueue_fx_sync(
        session,
        minimum_interval_seconds=settings.fx_min_sync_interval_seconds,
    )
    return _job_response(job, duplicate=duplicate)


@router.get(
    "/sync",
    response_model=FxSyncJobResponse | None,
    summary="Get the latest public FX synchronization job",
    description="Returns the latest scheduled or manual job, including after browser reload.",
)
async def latest_fx_sync_route(
    session: Annotated[AsyncSession, Depends(get_db_session)],
) -> FxSyncJobResponse | None:
    job = await get_latest_fx_sync_job(session)
    return _job_response(job, duplicate=False) if job is not None else None


@router.get(
    "/sync/{job_id}",
    response_model=FxSyncJobResponse,
    responses=NOT_FOUND_RESPONSE,
    summary="Get public FX synchronization status",
    description=(
        "Returns queue state and a stable safe error code, without an external response body or "
        "financial values."
    ),
)
async def get_fx_sync_route(
    job_id: UUID,
    session: Annotated[AsyncSession, Depends(get_db_session)],
) -> FxSyncJobResponse:
    job = await get_fx_sync_job(session, job_id)
    if job is None:
        not_found("fx_sync_job")
    return _job_response(job, duplicate=False)


def _resolved_response(resolved: ResolvedFxRate) -> FxRateResolveResponse:
    diagnostics: list[FxRateDiagnosticResponse] = []
    if resolved.diagnostic_code == "fx_rate_missing":
        diagnostics.append(
            FxRateDiagnosticResponse(
                severity="error",
                code="fx_rate_missing",
                message="No eligible exchange rate is available for the requested pair",
            )
        )
    elif resolved.diagnostic_code == "fx_rate_stale":
        diagnostics.append(
            FxRateDiagnosticResponse(
                severity="warning",
                code="fx_rate_stale",
                message="The selected exchange-rate observation is stale",
            )
        )
    return FxRateResolveResponse(
        base_currency=resolved.base_currency,
        quote_currency=resolved.quote_currency,
        valuation_as_of=resolved.valuation_as_of,
        status=resolved.status,
        rate=resolved.rate,
        path=resolved.path,
        observations=[
            FxRateUseResponse(
                exchange_rate_id=item.candidate.id,
                base_currency=item.candidate.base_currency,
                quote_currency=item.candidate.quote_currency,
                rate=item.candidate.rate,
                observed_at=item.candidate.observed_at,
                age_seconds=item.age_seconds,
                provider=item.candidate.provider,
                source=item.candidate.source,
                mode=item.candidate.mode,
            )
            for item in resolved.observations
        ],
        diagnostics=diagnostics,
    )


def _job_response(job: Any, *, duplicate: bool) -> FxSyncJobResponse:
    error_code: str | None = None
    if isinstance(job.last_error, dict):
        value = job.last_error.get("code")
        if isinstance(value, str):
            error_code = value
    return FxSyncJobResponse(
        id=job.id,
        status=job.status,
        attempts=job.attempts,
        available_at=job.available_at,
        created_at=job.created_at,
        updated_at=job.updated_at,
        duplicate=duplicate,
        error_code=error_code,
    )
