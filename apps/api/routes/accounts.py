from __future__ import annotations

from typing import Annotated, Any
from uuid import UUID

from fastapi import APIRouter, Depends, Query, Response, status
from sqlalchemy.ext.asyncio import AsyncSession

from accounts.schemas import AccountCreate, AccountListResponse, AccountResponse, AccountUpdate
from accounts.service import (
    create_account,
    delete_account,
    get_account,
    list_accounts,
    update_account,
)
from apps.api.routes.common import commit_or_conflict, not_found
from portfolios.service import get_portfolio
from shared.database import get_db_session
from shared.errors import ErrorResponse

router = APIRouter(tags=["accounts"])

NOT_FOUND_RESPONSE: dict[int | str, dict[str, Any]] = {
    status.HTTP_404_NOT_FOUND: {
        "model": ErrorResponse,
        "description": "Portfolio or account was not found",
    }
}


@router.post(
    "/api/v1/portfolios/{portfolio_id}/accounts",
    response_model=AccountResponse,
    status_code=status.HTTP_201_CREATED,
    responses=NOT_FOUND_RESPONSE,
    summary="Create an account in a portfolio",
)
async def create_account_route(
    portfolio_id: UUID,
    payload: AccountCreate,
    session: Annotated[AsyncSession, Depends(get_db_session)],
) -> AccountResponse:
    if await get_portfolio(session, portfolio_id) is None:
        not_found("portfolio")
    account = await create_account(session, portfolio_id=portfolio_id, payload=payload)
    await commit_or_conflict(
        session,
        code="account_conflict",
        message="Account conflicts with existing data",
    )
    await session.refresh(account)
    return AccountResponse.model_validate(account)


@router.get(
    "/api/v1/portfolios/{portfolio_id}/accounts",
    response_model=AccountListResponse,
    responses=NOT_FOUND_RESPONSE,
    summary="List accounts in a portfolio",
)
async def list_accounts_route(
    portfolio_id: UUID,
    session: Annotated[AsyncSession, Depends(get_db_session)],
    limit: Annotated[int, Query(ge=1, le=100)] = 50,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> AccountListResponse:
    if await get_portfolio(session, portfolio_id) is None:
        not_found("portfolio")
    accounts = await list_accounts(
        session,
        portfolio_id=portfolio_id,
        limit=limit,
        offset=offset,
    )
    return AccountListResponse(
        items=[AccountResponse.model_validate(item) for item in accounts],
        limit=limit,
        offset=offset,
    )


@router.get(
    "/api/v1/accounts/{account_id}",
    response_model=AccountResponse,
    responses=NOT_FOUND_RESPONSE,
    summary="Get an account",
)
async def get_account_route(
    account_id: UUID,
    session: Annotated[AsyncSession, Depends(get_db_session)],
) -> AccountResponse:
    account = await get_account(session, account_id)
    if account is None:
        not_found("account")
    return AccountResponse.model_validate(account)


@router.patch(
    "/api/v1/accounts/{account_id}",
    response_model=AccountResponse,
    responses=NOT_FOUND_RESPONSE,
    summary="Update an account",
)
async def update_account_route(
    account_id: UUID,
    payload: AccountUpdate,
    session: Annotated[AsyncSession, Depends(get_db_session)],
) -> AccountResponse:
    account = await get_account(session, account_id)
    if account is None:
        not_found("account")
    update_account(account, payload)
    await commit_or_conflict(
        session,
        code="account_conflict",
        message="Account conflicts with existing data",
    )
    await session.refresh(account)
    return AccountResponse.model_validate(account)


@router.delete(
    "/api/v1/accounts/{account_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    responses=NOT_FOUND_RESPONSE,
    summary="Delete an account",
)
async def delete_account_route(
    account_id: UUID,
    session: Annotated[AsyncSession, Depends(get_db_session)],
) -> Response:
    account = await get_account(session, account_id)
    if account is None:
        not_found("account")
    await delete_account(session, account)
    await commit_or_conflict(
        session,
        code="account_in_use",
        message="Account cannot be deleted while it is in use",
    )
    return Response(status_code=status.HTTP_204_NO_CONTENT)
