from __future__ import annotations

from datetime import datetime
from uuid import UUID

from sqlalchemy import Select, select
from sqlalchemy.ext.asyncio import AsyncSession

from operations.models import OperationModel, OperationSourceType, OperationType
from operations.schemas import OperationCreate


def payload_instrument_id(payload: OperationCreate) -> UUID | None:
    return getattr(payload.payload, "instrument_id", None)


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
        statement = statement.where(OperationModel.instrument_id == instrument_id)
    statement = (
        statement.order_by(OperationModel.occurred_at.desc(), OperationModel.id.desc())
        .limit(limit)
        .offset(offset)
    )
    return list(await session.scalars(statement))
