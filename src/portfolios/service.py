from __future__ import annotations

from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from portfolios.models import PortfolioModel
from portfolios.schemas import PortfolioCreate, PortfolioUpdate


async def create_portfolio(
    session: AsyncSession,
    payload: PortfolioCreate,
) -> PortfolioModel:
    portfolio = PortfolioModel(**payload.model_dump())
    session.add(portfolio)
    return portfolio


async def get_portfolio(session: AsyncSession, portfolio_id: UUID) -> PortfolioModel | None:
    return await session.get(PortfolioModel, portfolio_id)


async def list_portfolios(
    session: AsyncSession,
    *,
    limit: int,
    offset: int,
) -> list[PortfolioModel]:
    statement = (
        select(PortfolioModel)
        .order_by(PortfolioModel.created_at, PortfolioModel.id)
        .limit(limit)
        .offset(offset)
    )
    result = await session.scalars(statement)
    return list(result)


def update_portfolio(portfolio: PortfolioModel, payload: PortfolioUpdate) -> None:
    for field, value in payload.model_dump(exclude_unset=True, exclude_none=True).items():
        setattr(portfolio, field, value)


async def delete_portfolio(session: AsyncSession, portfolio: PortfolioModel) -> None:
    await session.delete(portfolio)
