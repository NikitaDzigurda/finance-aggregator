from __future__ import annotations

from collections.abc import Collection
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from instruments.models import InstrumentIdentifierModel, InstrumentModel
from instruments.schemas import InstrumentCreate, InstrumentUpdate


def _new_identifier(payload: object) -> InstrumentIdentifierModel:
    from instruments.schemas import InstrumentIdentifierCreate

    assert isinstance(payload, InstrumentIdentifierCreate)
    return InstrumentIdentifierModel(**payload.model_dump())


async def create_instrument(
    session: AsyncSession,
    payload: InstrumentCreate,
) -> InstrumentModel:
    values = payload.model_dump(exclude={"identifiers"})
    instrument = InstrumentModel(**values)
    instrument.identifiers = [_new_identifier(identifier) for identifier in payload.identifiers]
    session.add(instrument)
    return instrument


async def get_instrument(session: AsyncSession, instrument_id: UUID) -> InstrumentModel | None:
    result = await session.scalars(
        select(InstrumentModel)
        .options(selectinload(InstrumentModel.identifiers))
        .where(InstrumentModel.id == instrument_id)
    )
    return result.one_or_none()


async def list_instruments(
    session: AsyncSession,
    *,
    limit: int,
    offset: int,
) -> list[InstrumentModel]:
    result = await session.scalars(
        select(InstrumentModel)
        .options(selectinload(InstrumentModel.identifiers))
        .order_by(InstrumentModel.created_at, InstrumentModel.id)
        .limit(limit)
        .offset(offset)
    )
    return list(result)


async def get_instruments_by_ids(
    session: AsyncSession,
    instrument_ids: Collection[UUID],
) -> list[InstrumentModel]:
    if not instrument_ids:
        return []
    result = await session.scalars(
        select(InstrumentModel)
        .options(selectinload(InstrumentModel.identifiers))
        .where(InstrumentModel.id.in_(instrument_ids))
        .order_by(InstrumentModel.id)
    )
    return list(result)


async def update_instrument(
    session: AsyncSession,
    instrument: InstrumentModel,
    payload: InstrumentUpdate,
) -> None:
    values = payload.model_dump(exclude={"identifiers"}, exclude_unset=True, exclude_none=True)
    for field, value in values.items():
        setattr(instrument, field, value)

    if payload.identifiers is not None:
        instrument.identifiers.clear()
        await session.flush()
        instrument.identifiers.extend(
            _new_identifier(identifier) for identifier in payload.identifiers
        )


async def delete_instrument(session: AsyncSession, instrument: InstrumentModel) -> None:
    await session.delete(instrument)
