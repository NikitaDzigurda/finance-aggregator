from __future__ import annotations

from datetime import datetime
from uuid import UUID

from sqlalchemy import Select, select
from sqlalchemy.ext.asyncio import AsyncSession

from pricing.models import MarketPriceModel
from pricing.schemas import MarketPriceCreate


def create_market_price(payload: MarketPriceCreate) -> MarketPriceModel:
    return MarketPriceModel(
        instrument_id=payload.instrument_id,
        price=payload.price,
        currency=payload.currency,
        observed_at=payload.observed_at,
    )


async def list_market_prices(
    session: AsyncSession,
    *,
    instrument_id: UUID | None,
    observed_from: datetime | None,
    observed_to: datetime | None,
    limit: int,
    offset: int,
) -> list[MarketPriceModel]:
    statement: Select[tuple[MarketPriceModel]] = select(MarketPriceModel)
    if instrument_id is not None:
        statement = statement.where(MarketPriceModel.instrument_id == instrument_id)
    if observed_from is not None:
        statement = statement.where(MarketPriceModel.observed_at >= observed_from)
    if observed_to is not None:
        statement = statement.where(MarketPriceModel.observed_at <= observed_to)
    statement = (
        statement.order_by(MarketPriceModel.observed_at.desc(), MarketPriceModel.id.desc())
        .limit(limit)
        .offset(offset)
    )
    return list(await session.scalars(statement))
