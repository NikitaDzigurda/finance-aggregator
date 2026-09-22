from __future__ import annotations

from collections.abc import Collection
from datetime import datetime
from uuid import UUID

from sqlalchemy import Select, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from operations.models import OperationModel, OperationSourceType, OperationType
from operations.schemas import CryptoTradePayload, OperationCreate


def payload_instrument_id(payload: OperationCreate) -> UUID | None:
    if isinstance(payload.payload, CryptoTradePayload):
        return payload.payload.bought_instrument_id
    return getattr(payload.payload, "instrument_id", None)


def payload_secondary_instrument_id(payload: OperationCreate) -> UUID | None:
    if isinstance(payload.payload, CryptoTradePayload):
        return payload.payload.sold_instrument_id
    return None


def payload_instrument_ids(payload: OperationCreate) -> tuple[UUID, ...]:
    values = (payload_instrument_id(payload), payload_secondary_instrument_id(payload))
    return tuple(value for value in values if value is not None)


def create_manual_operation(payload: OperationCreate) -> OperationModel:
    return OperationModel(
        portfolio_id=payload.portfolio_id,
        account_id=payload.account_id,
        operation_type=payload.operation_type,
        occurred_at=payload.occurred_at,
        time_precision=payload.time_precision,
        source_type=OperationSourceType.MANUAL,
        payload=payload.payload.model_dump(mode="json"),
        instrument_id=payload_instrument_id(payload),
        secondary_instrument_id=payload_secondary_instrument_id(payload),
        correction_of_operation_id=payload.correction_of_operation_id,
        note=payload.note,
    )


async def get_operation(session: AsyncSession, operation_id: UUID) -> OperationModel | None:
    return await session.get(OperationModel, operation_id)


async def list_operations(
    session: AsyncSession,
    *,
    portfolio_id: UUID | None,
    account_id: UUID | None,
    operation_type: OperationType | None,
    occurred_from: datetime | None,
    occurred_to: datetime | None,
    instrument_id: UUID | None,
    limit: int,
    offset: int,
) -> list[OperationModel]:
    statement: Select[tuple[OperationModel]] = select(OperationModel)
    if portfolio_id is not None:
        statement = statement.where(OperationModel.portfolio_id == portfolio_id)
    if account_id is not None:
        statement = statement.where(OperationModel.account_id == account_id)
    if operation_type is not None:
        statement = statement.where(OperationModel.operation_type == operation_type)
    if occurred_from is not None:
        statement = statement.where(OperationModel.occurred_at >= occurred_from)
    if occurred_to is not None:
        statement = statement.where(OperationModel.occurred_at <= occurred_to)
    if instrument_id is not None:
        statement = statement.where(
            or_(
                OperationModel.instrument_id == instrument_id,
                OperationModel.secondary_instrument_id == instrument_id,
            )
        )
    statement = (
        statement.order_by(OperationModel.occurred_at.desc(), OperationModel.id.desc())
        .limit(limit)
        .offset(offset)
    )
    return list(await session.scalars(statement))


async def count_portfolio_operations(
    session: AsyncSession,
    *,
    portfolio_id: UUID,
) -> int:
    count = await session.scalar(
        select(func.count())
        .select_from(OperationModel)
        .where(OperationModel.portfolio_id == portfolio_id)
    )
    return int(count or 0)


async def portfolio_ids_using_instruments(
    session: AsyncSession, instrument_ids: Collection[UUID]
) -> list[UUID]:
    """Public application query for portfolios touched by canonical instruments."""
    if not instrument_ids:
        return []
    rows = await session.scalars(
        select(OperationModel.portfolio_id)
        .where(
            or_(
                OperationModel.instrument_id.in_(instrument_ids),
                OperationModel.secondary_instrument_id.in_(instrument_ids),
            )
        )
        .distinct()
        .order_by(OperationModel.portfolio_id)
    )
    return list(rows)


async def instrument_ids_used_by_operations(
    session: AsyncSession, instrument_ids: Collection[UUID]
) -> set[UUID]:
    """Return only instruments represented in persisted portfolio operations."""
    if not instrument_ids:
        return set()
    primary = await session.scalars(
        select(OperationModel.instrument_id)
        .where(OperationModel.instrument_id.in_(instrument_ids))
        .distinct()
    )
    secondary = await session.scalars(
        select(OperationModel.secondary_instrument_id)
        .where(OperationModel.secondary_instrument_id.in_(instrument_ids))
        .distinct()
    )
    return {value for value in (*primary, *secondary) if value is not None}


async def list_portfolio_operations_before(
    session: AsyncSession,
    *,
    portfolio_id: UUID,
    occurred_before: datetime,
    account_ids: Collection[UUID],
) -> list[OperationModel]:
    if not account_ids:
        return []
    statement: Select[tuple[OperationModel]] = (
        select(OperationModel)
        .where(
            OperationModel.portfolio_id == portfolio_id,
            OperationModel.account_id.in_(account_ids),
            OperationModel.occurred_at < occurred_before,
        )
        .order_by(OperationModel.occurred_at, OperationModel.id)
    )
    return list(await session.scalars(statement))
