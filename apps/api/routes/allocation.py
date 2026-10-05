from __future__ import annotations

from typing import Annotated, Any, Never
from uuid import UUID

from fastapi import APIRouter, Depends, Response, status
from sqlalchemy.ext.asyncio import AsyncSession

from allocation.schemas import (
    AllocationCategoryCreate,
    AllocationCategoryListResponse,
    AllocationCategoryResponse,
    AllocationCategoryUpdate,
    InstrumentCategoryAssignmentRequest,
    InstrumentCategoryAssignmentResponse,
)
from allocation.service import (
    assign_instrument_category,
    clear_instrument_category,
    create_category,
    delete_category,
    get_visible_category,
    list_categories,
    resolve_instrument_category,
    update_category,
)
from apps.api.routes.common import commit_or_conflict
from instruments.service import get_instrument
from shared.database import get_db_session
from shared.errors import ApiErrorException, ErrorResponse

router = APIRouter(prefix="/api/v1/portfolios", tags=["allocation"])

ERROR_RESPONSES: dict[int | str, dict[str, Any]] = {
    status.HTTP_404_NOT_FOUND: {
        "model": ErrorResponse,
        "description": "Portfolio, instrument, or allocation category was not found",
    },
    status.HTTP_409_CONFLICT: {
        "model": ErrorResponse,
        "description": "A system or used category cannot be changed or deleted",
    },
}


@router.get(
    "/{portfolio_id}/allocation/categories",
    response_model=AllocationCategoryListResponse,
    responses=ERROR_RESPONSES,
    summary="List system and portfolio allocation categories",
    description=(
        "Returns the seven immutable system classes and custom categories visible only "
        "inside this portfolio. No sector, country, or issuer taxonomy is inferred."
    ),
)
async def list_categories_route(
    portfolio_id: UUID,
    session: Annotated[AsyncSession, Depends(get_db_session)],
) -> AllocationCategoryListResponse:
    categories = await list_categories(session, portfolio_id=portfolio_id)
    return AllocationCategoryListResponse(
        items=[AllocationCategoryResponse.model_validate(item) for item in categories]
    )


@router.post(
    "/{portfolio_id}/allocation/categories",
    response_model=AllocationCategoryResponse,
    status_code=status.HTTP_201_CREATED,
    responses=ERROR_RESPONSES,
    summary="Create a portfolio-specific allocation category",
    description=(
        "Creates a custom category scoped to one portfolio and associates it with one "
        "system asset class for stable aggregation."
    ),
)
async def create_category_route(
    portfolio_id: UUID,
    payload: AllocationCategoryCreate,
    session: Annotated[AsyncSession, Depends(get_db_session)],
) -> AllocationCategoryResponse:
    category = await create_category(session, portfolio_id=portfolio_id, payload=payload)
    await commit_or_conflict(
        session,
        code="allocation_category_conflict",
        message="Allocation category conflicts with existing data",
    )
    await session.refresh(category)
    return AllocationCategoryResponse.model_validate(category)


@router.patch(
    "/{portfolio_id}/allocation/categories/{category_id}",
    response_model=AllocationCategoryResponse,
    responses=ERROR_RESPONSES,
    summary="Update a portfolio-specific allocation category",
    description=(
        "Updates the custom name or its system-class grouping. Immutable system "
        "categories cannot be changed."
    ),
)
async def update_category_route(
    portfolio_id: UUID,
    category_id: UUID,
    payload: AllocationCategoryUpdate,
    session: Annotated[AsyncSession, Depends(get_db_session)],
) -> AllocationCategoryResponse:
    category = await get_visible_category(
        session, portfolio_id=portfolio_id, category_id=category_id
    )
    if category is None:
        _category_not_found()
    await update_category(category, payload)
    await commit_or_conflict(
        session,
        code="allocation_category_conflict",
        message="Allocation category conflicts with existing data",
    )
    await session.refresh(category)
    return AllocationCategoryResponse.model_validate(category)


@router.delete(
    "/{portfolio_id}/allocation/categories/{category_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    responses=ERROR_RESPONSES,
    summary="Delete an unused portfolio-specific allocation category",
    description=(
        "System categories are immutable. A custom category referenced by an instrument "
        "override cannot be deleted until it is explicitly reassigned or cleared."
    ),
)
async def delete_category_route(
    portfolio_id: UUID,
    category_id: UUID,
    session: Annotated[AsyncSession, Depends(get_db_session)],
) -> Response:
    category = await get_visible_category(
        session, portfolio_id=portfolio_id, category_id=category_id
    )
    if category is None:
        _category_not_found()
    await delete_category(session, category)
    await commit_or_conflict(
        session,
        code="allocation_category_in_use",
        message="Allocation category must be reassigned before deletion",
    )
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.get(
    "/{portfolio_id}/allocation/instrument-categories/{instrument_id}",
    response_model=InstrumentCategoryAssignmentResponse,
    responses=ERROR_RESPONSES,
    summary="Get the effective category of an instrument",
    description="Returns a portfolio override when present, otherwise the system default.",
)
async def get_instrument_category_route(
    portfolio_id: UUID,
    instrument_id: UUID,
    session: Annotated[AsyncSession, Depends(get_db_session)],
) -> InstrumentCategoryAssignmentResponse:
    instrument = await get_instrument(session, instrument_id)
    if instrument is None:
        raise ApiErrorException(
            status_code=status.HTTP_404_NOT_FOUND,
            code="instrument_not_found",
            message="Instrument was not found",
        )
    category, overridden = await resolve_instrument_category(
        session, portfolio_id=portfolio_id, instrument=instrument
    )
    return _assignment_response(portfolio_id, instrument_id, category, overridden)


@router.put(
    "/{portfolio_id}/allocation/instrument-categories/{instrument_id}",
    response_model=InstrumentCategoryAssignmentResponse,
    responses=ERROR_RESPONSES,
    summary="Set a portfolio-specific instrument category override",
    description=(
        "Atomically replaces this portfolio's only override for the instrument; the "
        "global instrument and every other portfolio remain unchanged."
    ),
)
async def assign_instrument_category_route(
    portfolio_id: UUID,
    instrument_id: UUID,
    payload: InstrumentCategoryAssignmentRequest,
    session: Annotated[AsyncSession, Depends(get_db_session)],
) -> InstrumentCategoryAssignmentResponse:
    _, category = await assign_instrument_category(
        session,
        portfolio_id=portfolio_id,
        instrument_id=instrument_id,
        category_id=payload.category_id,
    )
    await commit_or_conflict(
        session,
        code="allocation_assignment_conflict",
        message="Instrument category assignment conflicts with existing data",
    )
    return _assignment_response(portfolio_id, instrument_id, category, True)


@router.delete(
    "/{portfolio_id}/allocation/instrument-categories/{instrument_id}",
    response_model=InstrumentCategoryAssignmentResponse,
    responses=ERROR_RESPONSES,
    summary="Clear an instrument category override",
    description="Removes only this portfolio's override and returns the system default.",
)
async def clear_instrument_category_route(
    portfolio_id: UUID,
    instrument_id: UUID,
    session: Annotated[AsyncSession, Depends(get_db_session)],
) -> InstrumentCategoryAssignmentResponse:
    instrument = await clear_instrument_category(
        session, portfolio_id=portfolio_id, instrument_id=instrument_id
    )
    await session.commit()
    category, _ = await resolve_instrument_category(
        session, portfolio_id=portfolio_id, instrument=instrument
    )
    return _assignment_response(portfolio_id, instrument_id, category, False)
def _assignment_response(
    portfolio_id: UUID,
    instrument_id: UUID,
    category: object,
    overridden: bool,
) -> InstrumentCategoryAssignmentResponse:
    return InstrumentCategoryAssignmentResponse(
        portfolio_id=portfolio_id,
        instrument_id=instrument_id,
        source="override" if overridden else "default",
        category=AllocationCategoryResponse.model_validate(category),
    )


def _category_not_found() -> Never:
    raise ApiErrorException(
        status_code=status.HTTP_404_NOT_FOUND,
        code="allocation_category_not_found",
        message="Allocation category was not found",
    )
