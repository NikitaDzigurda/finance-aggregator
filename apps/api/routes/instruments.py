from __future__ import annotations

from typing import Annotated, Any
from uuid import UUID

from fastapi import APIRouter, Depends, Query, Response, status
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


@router.post(
    "",
    response_model=InstrumentResponse,
    status_code=status.HTTP_201_CREATED,
    responses=CONFLICT_RESPONSE,
    summary="Create an instrument with stable identifiers",
)
async def create_instrument_route(
    payload: InstrumentCreate,
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
    "/{instrument_id}",
    response_model=InstrumentResponse,
    responses=NOT_FOUND_RESPONSE,
    summary="Get an instrument",
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
