from __future__ import annotations

from typing import Annotated, Any, Literal
from uuid import UUID

from fastapi import APIRouter, Body, Depends, Query, Response, status
from sqlalchemy.ext.asyncio import AsyncSession

from apps.api.routes.common import commit_or_conflict, not_found
from instruments.schemas import (
    InstrumentCreate,
    InstrumentListResponse,
    InstrumentResponse,
    InstrumentUpdate,
)
from instruments.service import (
    create_instrument,
    delete_instrument,
    get_instrument,
    list_instruments,
    update_instrument,
)
from pricing.market_data import list_market_mappings, mapping_matches_instrument
from pricing.schemas import MarketMappingListResponse, MarketMappingResponse
from shared.database import get_db_session
from shared.errors import ErrorResponse

router = APIRouter(prefix="/api/v1/instruments", tags=["instruments"])

NOT_FOUND_RESPONSE: dict[int | str, dict[str, Any]] = {
    status.HTTP_404_NOT_FOUND: {
        "model": ErrorResponse,
        "description": "Instrument was not found",
    }
}
CONFLICT_RESPONSE: dict[int | str, dict[str, Any]] = {
    status.HTTP_409_CONFLICT: {
        "model": ErrorResponse,
        "description": "An identifier is already assigned to another instrument",
    }
}
INSTRUMENT_EXAMPLES = {
    "listed_security": {
        "summary": "Listed security with scoped ticker",
        "value": {
            "name": "Synthetic Equity",
            "instrument_type": "stock",
            "currency": "USD",
            "identifiers": [
                {
                    "identifier_type": "ticker",
                    "value": "SYN",
                    "exchange": "XNAS",
                }
            ],
        },
    },
    "crypto_asset": {
        "summary": "Crypto asset with stable asset code",
        "value": {
            "name": "Synthetic Coin",
            "instrument_type": "crypto_asset",
            "currency": "USD",
            "identifiers": [
                {
                    "identifier_type": "crypto_asset_code",
                    "value": "SYNCOIN",
                }
            ],
        },
    },
}


@router.post(
    "",
    response_model=InstrumentResponse,
    status_code=status.HTTP_201_CREATED,
    responses=CONFLICT_RESPONSE,
    summary="Create an instrument with stable identifiers",
    description=(
        "Creates one canonical instrument. Tickers require an exchange, provider codes require "
        "a provider, and identifiers cannot already belong to another instrument."
    ),
)
async def create_instrument_route(
    payload: Annotated[InstrumentCreate, Body(openapi_examples=INSTRUMENT_EXAMPLES)],
    session: Annotated[AsyncSession, Depends(get_db_session)],
) -> InstrumentResponse:
    instrument = await create_instrument(session, payload)
    await commit_or_conflict(
        session,
        code="instrument_identifier_conflict",
        message="One or more instrument identifiers are already in use",
    )
    await session.refresh(instrument, attribute_names=["identifiers"])
    return InstrumentResponse.model_validate(instrument)


@router.get(
    "",
    response_model=InstrumentListResponse,
    summary="List instruments",
    description="Returns a bounded page of canonical instruments with all stored identifiers.",
)
async def list_instruments_route(
    session: Annotated[AsyncSession, Depends(get_db_session)],
    limit: Annotated[int, Query(ge=1, le=100)] = 50,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> InstrumentListResponse:
    instruments = await list_instruments(session, limit=limit, offset=offset)
    return InstrumentListResponse(
        items=[InstrumentResponse.model_validate(item) for item in instruments],
        limit=limit,
        offset=offset,
    )


@router.get(
    "/{instrument_id}/market-mappings",
    response_model=MarketMappingListResponse,
    responses=NOT_FOUND_RESPONSE,
    summary="Inspect reviewed market-data mappings for an instrument",
    description=(
        "Shows why an asset is or is not eligible for automatic pricing. "
        "An absent or ambiguous mapping never guesses a symbol."
    ),
)
async def list_market_mappings_route(
    instrument_id: UUID,
    session: Annotated[AsyncSession, Depends(get_db_session)],
) -> MarketMappingListResponse:
    instrument = await get_instrument(session, instrument_id)
    if instrument is None:
        not_found("instrument")
    mappings = await list_market_mappings(session, instrument_id)
    items = [
        MarketMappingResponse.model_validate(
            {
                **{
                    field: getattr(mapping, field)
                    for field in MarketMappingResponse.model_fields
                    if field != "eligible"
                },
                "eligible": mapping.status == "verified"
                and mapping_matches_instrument(mapping, instrument),
            },
        )
        for mapping in mappings
    ]
    mapping_status: Literal["verified", "ambiguous", "unsupported", "unmapped"]
    if any(item.eligible for item in items):
        mapping_status = "verified"
    elif any(item.status == "ambiguous" for item in items):
        mapping_status = "ambiguous"
    elif items:
        mapping_status = "unsupported"
    else:
        mapping_status = "unmapped"
    return MarketMappingListResponse(
        instrument_id=instrument_id, status=mapping_status, items=items
    )


@router.get(
    "/{instrument_id}",
    response_model=InstrumentResponse,
    responses=NOT_FOUND_RESPONSE,
    summary="Get an instrument",
    description="Returns one canonical instrument and its stable identifier set.",
)
async def get_instrument_route(
    instrument_id: UUID,
    session: Annotated[AsyncSession, Depends(get_db_session)],
) -> InstrumentResponse:
    instrument = await get_instrument(session, instrument_id)
    if instrument is None:
        not_found("instrument")
    return InstrumentResponse.model_validate(instrument)


@router.patch(
    "/{instrument_id}",
    response_model=InstrumentResponse,
    responses=NOT_FOUND_RESPONSE | CONFLICT_RESPONSE,
    summary="Update an instrument",
    description=(
        "Changes supplied fields. When identifiers are supplied, the entire identifier set is "
        "validated and replaced atomically."
    ),
)
async def update_instrument_route(
    instrument_id: UUID,
    payload: InstrumentUpdate,
    session: Annotated[AsyncSession, Depends(get_db_session)],
) -> InstrumentResponse:
    instrument = await get_instrument(session, instrument_id)
    if instrument is None:
        not_found("instrument")
    await update_instrument(session, instrument, payload)
    await commit_or_conflict(
        session,
        code="instrument_identifier_conflict",
        message="One or more instrument identifiers are already in use",
    )
    await session.refresh(instrument, attribute_names=["identifiers"])
    return InstrumentResponse.model_validate(instrument)


@router.delete(
    "/{instrument_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    responses=NOT_FOUND_RESPONSE | CONFLICT_RESPONSE,
    summary="Delete an instrument",
    description=(
        "Deletes an unused instrument. Ledger or price references cause a conflict so historical "
        "data remains traceable."
    ),
)
async def delete_instrument_route(
    instrument_id: UUID,
    session: Annotated[AsyncSession, Depends(get_db_session)],
) -> Response:
    instrument = await get_instrument(session, instrument_id)
    if instrument is None:
        not_found("instrument")
    await delete_instrument(session, instrument)
    await commit_or_conflict(
        session,
        code="instrument_in_use",
        message="Instrument cannot be deleted while it is in use",
    )
    return Response(status_code=status.HTTP_204_NO_CONTENT)
