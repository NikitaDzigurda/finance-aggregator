from __future__ import annotations

from typing import Annotated, Any
from uuid import UUID

from fastapi import APIRouter, Depends, Query, status
from sqlalchemy.ext.asyncio import AsyncSession

from accounts.service import get_account
from apps.api.routes.common import commit_or_conflict, not_found
from instruments.service import get_instrument
from operations.models import OperationType
from operations.schemas import (
    OperationCreate,
    OperationListResponse,
    OperationResponse,
    operation_response,
)
from operations.service import (
    create_manual_operation,
    get_operation,
    list_operations,
    payload_instrument_id,
)
from shared.database import get_db_session
from shared.errors import ApiErrorException, ErrorResponse
from shared.exact import AwareDateTime

router = APIRouter(prefix="/api/v1/operations", tags=["operations"])

NOT_FOUND_RESPONSE: dict[int | str, dict[str, Any]] = {
    status.HTTP_404_NOT_FOUND: {
        "model": ErrorResponse,
        "description": "Referenced account, instrument, or operation was not found",
    }
}


@router.post(
    "",
    response_model=OperationResponse,
    status_code=status.HTTP_201_CREATED,
    responses=NOT_FOUND_RESPONSE,
    summary="Create a manual ledger operation",
)
async def create_operation_route(
    payload: OperationCreate,
    session: Annotated[AsyncSession, Depends(get_db_session)],
) -> OperationResponse:
    account = await get_account(session, payload.account_id)
    if account is None or account.portfolio_id != payload.portfolio_id:
        not_found("account")

    instrument_id = payload_instrument_id(payload)
    if instrument_id is not None and await get_instrument(session, instrument_id) is None:
        not_found("instrument")

    if payload.correction_of_operation_id is not None:
        corrected = await get_operation(session, payload.correction_of_operation_id)
        if (
            corrected is None
            or corrected.portfolio_id != payload.portfolio_id
            or corrected.account_id != payload.account_id
        ):
            not_found("corrected_operation")

    operation = create_manual_operation(payload)
    session.add(operation)
    await commit_or_conflict(
        session,
        code="operation_conflict",
        message="Operation conflicts with existing ledger data",
    )
    await session.refresh(operation)
    return operation_response(operation)


@router.get(
    "",
    response_model=OperationListResponse,
    summary="List and filter ledger operations",
)
async def list_operations_route(
    session: Annotated[AsyncSession, Depends(get_db_session)],
    portfolio_id: UUID | None = None,
    account_id: UUID | None = None,
    operation_type: OperationType | None = None,
    occurred_from: AwareDateTime | None = None,
    occurred_to: AwareDateTime | None = None,
    instrument_id: UUID | None = None,
    limit: Annotated[int, Query(ge=1, le=100)] = 50,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> OperationListResponse:
    if (
        occurred_from is not None
        and occurred_to is not None
        and occurred_from > occurred_to
    ):
        raise ApiErrorException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            code="operation_period_invalid",
            message="occurred_from must not be later than occurred_to",
        )
    operations = await list_operations(
        session,
        portfolio_id=portfolio_id,
        account_id=account_id,
        operation_type=operation_type,
        occurred_from=occurred_from,
        occurred_to=occurred_to,
        instrument_id=instrument_id,
        limit=limit,
        offset=offset,
    )
    return OperationListResponse(
        items=[operation_response(item) for item in operations],
        limit=limit,
        offset=offset,
    )


@router.get(
    "/{operation_id}",
    response_model=OperationResponse,
    responses=NOT_FOUND_RESPONSE,
    summary="Get a ledger operation",
)
async def get_operation_route(
    operation_id: UUID,
    session: Annotated[AsyncSession, Depends(get_db_session)],
) -> OperationResponse:
    operation = await get_operation(session, operation_id)
    if operation is None:
        not_found("operation")
    return operation_response(operation)
