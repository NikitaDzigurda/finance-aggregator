from __future__ import annotations

from collections.abc import Sequence
from typing import Never
from uuid import UUID

from fastapi import status
from sqlalchemy import Select, delete, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from allocation.models import (
    AllocationCategoryModel,
    AssetClass,
    InstrumentCategoryOverrideModel,
)
from allocation.schemas import (
    AllocationCategoryCreate,
    AllocationCategoryUpdate,
)
from instruments.models import InstrumentModel, InstrumentType
from instruments.service import get_instrument
from portfolios.service import get_portfolio
from shared.errors import ApiErrorException

SYSTEM_CATEGORY_IDS: dict[AssetClass, UUID] = {
    AssetClass.EQUITY: UUID("10000000-0000-4000-8000-000000000001"),
    AssetClass.FIXED_INCOME: UUID("10000000-0000-4000-8000-000000000002"),
    AssetClass.FUND: UUID("10000000-0000-4000-8000-000000000003"),
    AssetClass.DERIVATIVE: UUID("10000000-0000-4000-8000-000000000004"),
    AssetClass.CRYPTO: UUID("10000000-0000-4000-8000-000000000005"),
    AssetClass.CASH: UUID("10000000-0000-4000-8000-000000000006"),
    AssetClass.OTHER: UUID("10000000-0000-4000-8000-000000000007"),
}

DEFAULT_ASSET_CLASS: dict[InstrumentType, AssetClass] = {
    InstrumentType.STOCK: AssetClass.EQUITY,
    InstrumentType.BOND: AssetClass.FIXED_INCOME,
    InstrumentType.ETF: AssetClass.FUND,
    InstrumentType.FUND: AssetClass.FUND,
    InstrumentType.OPTION: AssetClass.DERIVATIVE,
    InstrumentType.CRYPTO_ASSET: AssetClass.CRYPTO,
    InstrumentType.CURRENCY: AssetClass.CASH,
}


def default_asset_class(instrument_type: InstrumentType) -> AssetClass:
    return DEFAULT_ASSET_CLASS.get(instrument_type, AssetClass.OTHER)


async def list_categories(
    session: AsyncSession,
    *,
    portfolio_id: UUID,
) -> list[AllocationCategoryModel]:
    await _require_portfolio(session, portfolio_id)
    statement: Select[tuple[AllocationCategoryModel]] = (
        select(AllocationCategoryModel)
        .where(
            or_(
                AllocationCategoryModel.is_system.is_(True),
                AllocationCategoryModel.portfolio_id == portfolio_id,
            )
        )
        .order_by(
            AllocationCategoryModel.is_system.desc(),
            AllocationCategoryModel.system_class,
            AllocationCategoryModel.name,
            AllocationCategoryModel.id,
        )
    )
    return list(await session.scalars(statement))


async def get_visible_category(
    session: AsyncSession,
    *,
    portfolio_id: UUID,
    category_id: UUID,
) -> AllocationCategoryModel | None:
    statement: Select[tuple[AllocationCategoryModel]] = select(AllocationCategoryModel).where(
        AllocationCategoryModel.id == category_id,
        or_(
            AllocationCategoryModel.is_system.is_(True),
            AllocationCategoryModel.portfolio_id == portfolio_id,
        ),
    )
    result = await session.scalars(statement)
    return result.one_or_none()


async def create_category(
    session: AsyncSession,
    *,
    portfolio_id: UUID,
    payload: AllocationCategoryCreate,
) -> AllocationCategoryModel:
    await _require_portfolio(session, portfolio_id)
    category = AllocationCategoryModel(
        portfolio_id=portfolio_id,
        name=payload.name,
        system_class=payload.system_class,
        is_system=False,
    )
    session.add(category)
    return category


async def update_category(
    category: AllocationCategoryModel,
    payload: AllocationCategoryUpdate,
) -> None:
    if category.is_system:
        _immutable_system_category()
    for field, value in payload.model_dump(exclude_unset=True, exclude_none=True).items():
        setattr(category, field, value)


async def delete_category(
    session: AsyncSession,
    category: AllocationCategoryModel,
) -> None:
    if category.is_system:
        _immutable_system_category()
    await session.delete(category)


async def resolve_instrument_category(
    session: AsyncSession,
    *,
    portfolio_id: UUID,
    instrument: InstrumentModel,
) -> tuple[AllocationCategoryModel, bool]:
    override = await session.get(
        InstrumentCategoryOverrideModel,
        (portfolio_id, instrument.id),
    )
    if override is not None:
        category = await get_visible_category(
            session,
            portfolio_id=portfolio_id,
            category_id=override.category_id,
        )
        if category is None:
            raise RuntimeError("Stored allocation category override is not visible")
        return category, True
    asset_class = default_asset_class(instrument.instrument_type)
    category = await session.get(AllocationCategoryModel, SYSTEM_CATEGORY_IDS[asset_class])
    if category is None:
        raise RuntimeError("System allocation categories are not initialized")
    return category, False


async def assign_instrument_category(
    session: AsyncSession,
    *,
    portfolio_id: UUID,
    instrument_id: UUID,
    category_id: UUID,
) -> tuple[InstrumentModel, AllocationCategoryModel]:
    await _require_portfolio(session, portfolio_id)
    instrument = await get_instrument(session, instrument_id)
    if instrument is None:
        _not_found("instrument")
    category = await get_visible_category(
        session,
        portfolio_id=portfolio_id,
        category_id=category_id,
    )
    if category is None:
        _not_found("allocation_category")
    override = await session.get(
        InstrumentCategoryOverrideModel,
        (portfolio_id, instrument_id),
    )
    if override is None:
        session.add(
            InstrumentCategoryOverrideModel(
                portfolio_id=portfolio_id,
                instrument_id=instrument_id,
                category_id=category_id,
            )
        )
    else:
        override.category_id = category_id
    return instrument, category


async def clear_instrument_category(
    session: AsyncSession,
    *,
    portfolio_id: UUID,
    instrument_id: UUID,
) -> InstrumentModel:
    await _require_portfolio(session, portfolio_id)
    instrument = await get_instrument(session, instrument_id)
    if instrument is None:
        _not_found("instrument")
    await session.execute(
        delete(InstrumentCategoryOverrideModel).where(
            InstrumentCategoryOverrideModel.portfolio_id == portfolio_id,
            InstrumentCategoryOverrideModel.instrument_id == instrument_id,
        )
    )
    return instrument


async def resolve_categories_for_instruments(
    session: AsyncSession,
    *,
    portfolio_id: UUID,
    instruments: Sequence[InstrumentModel],
) -> dict[UUID, AllocationCategoryModel]:
    if not instruments:
        return {}
    overrides = {
        item.instrument_id: item.category_id
        for item in await session.scalars(
            select(InstrumentCategoryOverrideModel).where(
                InstrumentCategoryOverrideModel.portfolio_id == portfolio_id,
                InstrumentCategoryOverrideModel.instrument_id.in_(
                    [item.id for item in instruments]
                ),
            )
        )
    }
    category_ids = set(overrides.values()) | {
        SYSTEM_CATEGORY_IDS[default_asset_class(item.instrument_type)]
        for item in instruments
        if item.id not in overrides
    }
    categories = {
        item.id: item
        for item in await session.scalars(
            select(AllocationCategoryModel).where(AllocationCategoryModel.id.in_(category_ids))
        )
    }
    return {
        instrument.id: categories[
            overrides.get(
                instrument.id,
                SYSTEM_CATEGORY_IDS[default_asset_class(instrument.instrument_type)],
            )
        ]
        for instrument in instruments
    }
async def _require_portfolio(session: AsyncSession, portfolio_id: UUID) -> None:
    if await get_portfolio(session, portfolio_id) is None:
        _not_found("portfolio")


def _not_found(resource: str) -> Never:
    raise ApiErrorException(
        status_code=status.HTTP_404_NOT_FOUND,
        code=f"{resource}_not_found",
        message=f"{resource.replace('_', ' ').title()} was not found",
    )


def _immutable_system_category() -> Never:
    raise ApiErrorException(
        status_code=status.HTTP_409_CONFLICT,
        code="system_allocation_category_immutable",
        message="System allocation categories cannot be changed",
    )
