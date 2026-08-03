from __future__ import annotations

from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from accounts.models import AccountModel
from accounts.schemas import AccountCreate, AccountUpdate


async def create_account(
    session: AsyncSession,
    *,
    portfolio_id: UUID,
    payload: AccountCreate,
) -> AccountModel:
    account = AccountModel(portfolio_id=portfolio_id, **payload.model_dump())
    session.add(account)
    return account


async def get_account(session: AsyncSession, account_id: UUID) -> AccountModel | None:
    return await session.get(AccountModel, account_id)


async def list_accounts(
    session: AsyncSession,
    *,
    portfolio_id: UUID,
    limit: int,
    offset: int,
) -> list[AccountModel]:
    result = await session.scalars(
        select(AccountModel)
        .where(AccountModel.portfolio_id == portfolio_id)
        .order_by(AccountModel.created_at, AccountModel.id)
        .limit(limit)
        .offset(offset)
    )
    return list(result)


def update_account(account: AccountModel, payload: AccountUpdate) -> None:
    values = payload.model_dump(exclude_unset=True, exclude_none=True)
    for field, value in values.items():
        setattr(account, field, value)


async def delete_account(session: AsyncSession, account: AccountModel) -> None:
    await session.delete(account)
