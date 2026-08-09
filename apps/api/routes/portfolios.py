from __future__ import annotations

from typing import Annotated, Any
from uuid import UUID

from fastapi import APIRouter, Depends, Query, Response, status
from sqlalchemy.ext.asyncio import AsyncSession

from apps.api.routes.common import commit_or_conflict, not_found
from portfolios.schemas import (
    PortfolioCreate,
    PortfolioListResponse,
    PortfolioResponse,
    PortfolioUpdate,
)
from portfolios.service import (
    create_portfolio,
    delete_portfolio,
    get_portfolio,
    list_portfolios,
    update_portfolio,
)
from shared.database import get_db_session
from shared.errors import ErrorResponse

router = APIRouter(prefix="/api/v1/portfolios", tags=["portfolios"])

NOT_FOUND_RESPONSE: dict[int | str, dict[str, Any]] = {
    status.HTTP_404_NOT_FOUND: {
        "model": ErrorResponse,
        "description": "Portfolio was not found",
    }
}


@router.post(
    "",
    response_model=PortfolioResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Create a portfolio",
    description="Creates the top-level reporting container and its base currency.",
)
async def create_portfolio_route(
    payload: PortfolioCreate,
    session: Annotated[AsyncSession, Depends(get_db_session)],
) -> PortfolioResponse:
    portfolio = await create_portfolio(session, payload)
    await commit_or_conflict(
        session,
        code="portfolio_conflict",
        message="Portfolio conflicts with existing data",
    )
    await session.refresh(portfolio)
    return PortfolioResponse.model_validate(portfolio)


@router.get(
    "",
    response_model=PortfolioListResponse,
    summary="List portfolios",
    description="Returns a bounded page of portfolios ordered by creation time and ID.",
)
async def list_portfolios_route(
    session: Annotated[AsyncSession, Depends(get_db_session)],
    limit: Annotated[int, Query(ge=1, le=100)] = 50,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> PortfolioListResponse:
    portfolios = await list_portfolios(session, limit=limit, offset=offset)
    return PortfolioListResponse(
        items=[PortfolioResponse.model_validate(item) for item in portfolios],
        limit=limit,
        offset=offset,
    )


@router.get(
    "/{portfolio_id}",
    response_model=PortfolioResponse,
    responses=NOT_FOUND_RESPONSE,
    summary="Get a portfolio",
    description="Returns one portfolio without expanding its accounts or calculated positions.",
)
async def get_portfolio_route(
    portfolio_id: UUID,
    session: Annotated[AsyncSession, Depends(get_db_session)],
) -> PortfolioResponse:
    portfolio = await get_portfolio(session, portfolio_id)
    if portfolio is None:
        not_found("portfolio")
    return PortfolioResponse.model_validate(portfolio)


@router.patch(
    "/{portfolio_id}",
    response_model=PortfolioResponse,
    responses=NOT_FOUND_RESPONSE,
    summary="Update a portfolio",
    description="Changes supplied mutable fields; omitted fields keep their current values.",
)
async def update_portfolio_route(
    portfolio_id: UUID,
    payload: PortfolioUpdate,
    session: Annotated[AsyncSession, Depends(get_db_session)],
) -> PortfolioResponse:
    portfolio = await get_portfolio(session, portfolio_id)
    if portfolio is None:
        not_found("portfolio")
    update_portfolio(portfolio, payload)
    await commit_or_conflict(
        session,
        code="portfolio_conflict",
        message="Portfolio conflicts with existing data",
    )
    await session.refresh(portfolio)
    return PortfolioResponse.model_validate(portfolio)


@router.delete(
    "/{portfolio_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    responses=NOT_FOUND_RESPONSE,
    summary="Delete a portfolio and its accounts",
    description=(
        "Deletes the portfolio and unused child accounts. Ledger, import, price, or snapshot "
        "references cause a conflict instead of cascading financial history."
    ),
)
async def delete_portfolio_route(
    portfolio_id: UUID,
    session: Annotated[AsyncSession, Depends(get_db_session)],
) -> Response:
    portfolio = await get_portfolio(session, portfolio_id)
    if portfolio is None:
        not_found("portfolio")
    await delete_portfolio(session, portfolio)
    await commit_or_conflict(
        session,
        code="portfolio_in_use",
        message="Portfolio cannot be deleted while it is in use",
    )
    return Response(status_code=status.HTTP_204_NO_CONTENT)
