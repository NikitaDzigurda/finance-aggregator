from __future__ import annotations

from datetime import UTC, datetime
from typing import Annotated, Any
from uuid import UUID

from fastapi import APIRouter, Body, Depends, Query, status
from sqlalchemy.ext.asyncio import AsyncSession

from apps.api.routes.common import commit_or_conflict, not_found
from instruments.service import get_instrument
from pricing.schemas import (
    MarketPriceBatchCreate,
    MarketPriceBatchResponse,
    MarketPriceCreate,
    MarketPriceDiagnosticResponse,
    MarketPriceListResponse,
    MarketPriceResolveResponse,
    MarketPriceResponse,
)
from pricing.service import (
    create_market_price,
    create_market_prices,
    list_market_prices,
    market_price_stale_after_seconds,
    resolve_market_price,
)
from shared.config import get_settings
from shared.database import get_db_session
from shared.errors import ApiErrorException, ErrorResponse
from shared.exact import AwareDateTime, CurrencyCode

router = APIRouter(prefix="/api/v1/prices", tags=["pricing"])

NOT_FOUND_RESPONSE: dict[int | str, dict[str, Any]] = {
    status.HTTP_404_NOT_FOUND: {
        "model": ErrorResponse,
        "description": "Referenced instrument was not found",
    }
}
MARKET_PRICE_EXAMPLES = {
    "manual_price": {
        "summary": "Exact USD market price",
        "value": {
            "instrument_id": "33333333-3333-4333-8333-333333333333",
            "price": "130.75",
            "currency": "USD",
            "observed_at": "2026-08-10T18:00:00Z",
        },
    }
}
MARKET_PRICE_BATCH_EXAMPLES = {
    "manual_prices": {
        "summary": "Atomic exact price observations",
        "value": {
            "items": [
                {
                    "instrument_id": "33333333-3333-4333-8333-333333333333",
                    "price": "130.75",
                    "currency": "USD",
                    "observed_at": "2026-08-21T18:00:00Z",
                },
                {
                    "instrument_id": "44444444-4444-4444-8444-444444444444",
                    "price": "82.125",
                    "currency": "RUB",
                    "observed_at": "2026-08-21T18:00:00Z",
                },
            ]
        },
    }
}


@router.post(
    "/batch",
    response_model=MarketPriceBatchResponse,
    status_code=status.HTTP_201_CREATED,
    responses=NOT_FOUND_RESPONSE,
    summary="Record an atomic batch of manual market prices",
    description=(
        "Validates every instrument and exact append-only observation before one database "
        "commit. If any item is invalid or conflicts, no item from the batch is stored."
    ),
)
async def create_market_price_batch_route(
    payload: Annotated[
        MarketPriceBatchCreate,
        Body(openapi_examples=MARKET_PRICE_BATCH_EXAMPLES),
    ],
    session: Annotated[AsyncSession, Depends(get_db_session)],
) -> MarketPriceBatchResponse:
    for item in payload.items:
        if await get_instrument(session, item.instrument_id) is None:
            not_found("instrument")
    prices = create_market_prices(payload.items)
    session.add_all(prices)
    await commit_or_conflict(
        session,
        code="market_price_batch_conflict",
        message="Market price batch conflicts with existing pricing data",
    )
    for price in prices:
        await session.refresh(price)
    return MarketPriceBatchResponse(
        items=[MarketPriceResponse.model_validate(item) for item in prices]
    )


@router.get(
    "/resolve",
    response_model=MarketPriceResolveResponse,
    responses=NOT_FOUND_RESPONSE,
    summary="Resolve a market price at a valuation instant",
    description=(
        "Selects the latest append-only observation with observed_at not later than "
        "valuation_as_of. A future observation is never used; missing and stale states are "
        "returned explicitly with the selected observation age."
    ),
)
async def resolve_market_price_route(
    instrument_id: UUID,
    session: Annotated[AsyncSession, Depends(get_db_session)],
    currency: CurrencyCode | None = None,
    valuation_as_of: AwareDateTime | None = None,
) -> MarketPriceResolveResponse:
    if await get_instrument(session, instrument_id) is None:
        not_found("instrument")
    as_of = valuation_as_of or datetime.now(UTC)
    price = await resolve_market_price(
        session,
        instrument_id=instrument_id,
        currency=currency,
        valuation_as_of=as_of,
    )
    if price is None:
        return MarketPriceResolveResponse(
            instrument_id=instrument_id,
            valuation_as_of=as_of,
            status="unavailable",
            observation=None,
            age_seconds=None,
            diagnostics=[
                MarketPriceDiagnosticResponse(
                    severity="error",
                    code="market_price_missing",
                    message="No eligible market price is available",
                )
            ],
        )
    age_seconds = max(0, int((as_of - price.observed_at).total_seconds()))
    stale = age_seconds > market_price_stale_after_seconds(price, get_settings())
    return MarketPriceResolveResponse(
        instrument_id=instrument_id,
        valuation_as_of=as_of,
        status="stale" if stale else "fresh",
        observation=MarketPriceResponse.model_validate(price),
        age_seconds=age_seconds,
        diagnostics=(
            [
                MarketPriceDiagnosticResponse(
                    severity="warning",
                    code="market_price_stale",
                    message="The selected market-price observation is stale",
                )
            ]
            if stale
            else []
        ),
    )


@router.post(
    "",
    response_model=MarketPriceResponse,
    status_code=status.HTTP_201_CREATED,
    responses=NOT_FOUND_RESPONSE,
    summary="Record an exact manual market price",
    description=(
        "Stores a versioned positive price observation. Price is an exact decimal JSON string; "
        "existing observations are not overwritten."
    ),
)
async def create_market_price_route(
    payload: Annotated[MarketPriceCreate, Body(openapi_examples=MARKET_PRICE_EXAMPLES)],
    session: Annotated[AsyncSession, Depends(get_db_session)],
) -> MarketPriceResponse:
    if await get_instrument(session, payload.instrument_id) is None:
        not_found("instrument")
    price = create_market_price(payload)
    session.add(price)
    await commit_or_conflict(
        session,
        code="market_price_conflict",
        message="Market price conflicts with existing pricing data",
    )
    await session.refresh(price)
    return MarketPriceResponse.model_validate(price)


@router.get(
    "",
    response_model=MarketPriceListResponse,
    summary="List exact market prices",
    description=(
        "Returns price observations with optional instrument and inclusive observation-time "
        "filters, ordered newest first."
    ),
)
async def list_market_prices_route(
    session: Annotated[AsyncSession, Depends(get_db_session)],
    instrument_id: UUID | None = None,
    observed_from: AwareDateTime | None = None,
    observed_to: AwareDateTime | None = None,
    limit: Annotated[int, Query(ge=1, le=200)] = 100,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> MarketPriceListResponse:
    if (
        observed_from is not None
        and observed_to is not None
        and observed_from > observed_to
    ):
        raise ApiErrorException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            code="market_price_period_invalid",
            message="observed_from must not be later than observed_to",
        )
    prices = await list_market_prices(
        session,
        instrument_id=instrument_id,
        observed_from=observed_from,
        observed_to=observed_to,
        limit=limit,
        offset=offset,
    )
    return MarketPriceListResponse(
        items=[MarketPriceResponse.model_validate(item) for item in prices],
        limit=limit,
        offset=offset,
    )
