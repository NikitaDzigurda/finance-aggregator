from __future__ import annotations

from typing import Annotated, Any
from uuid import UUID

from fastapi import APIRouter, Body, Depends, Query, status
from sqlalchemy.ext.asyncio import AsyncSession

from apps.api.routes.common import commit_or_conflict, not_found
from instruments.service import get_instrument
from pricing.schemas import MarketPriceCreate, MarketPriceListResponse, MarketPriceResponse
from pricing.service import create_market_price, list_market_prices
from shared.database import get_db_session
from shared.errors import ApiErrorException, ErrorResponse
from shared.exact import AwareDateTime

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
    summary="List exact manual market prices",
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
