from __future__ import annotations

from decimal import Decimal
from typing import Annotated
from uuid import UUID

from pydantic import AfterValidator, BaseModel, ConfigDict
from pydantic_core import PydanticCustomError

from shared.exact import AwareDateTime, CurrencyCode, Price


def _positive(value: Decimal) -> Decimal:
    if value <= 0:
        raise PydanticCustomError("value_not_positive", "Value must be greater than zero")
    return value


type PositivePrice = Annotated[Price, AfterValidator(_positive)]


class MarketPriceCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    instrument_id: UUID
    price: PositivePrice
    currency: CurrencyCode
    observed_at: AwareDateTime


class MarketPriceResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True, extra="forbid")

    id: UUID
    instrument_id: UUID
    price: Price
    currency: CurrencyCode
    observed_at: AwareDateTime
    created_at: AwareDateTime
    updated_at: AwareDateTime


class MarketPriceListResponse(BaseModel):
    items: list[MarketPriceResponse]
    limit: int
    offset: int
