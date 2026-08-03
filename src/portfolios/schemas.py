from __future__ import annotations

from typing import Annotated
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, StringConstraints

from shared.exact import AwareDateTime, CurrencyCode

type ResourceName = Annotated[
    str,
    StringConstraints(strict=True, strip_whitespace=True, min_length=1, max_length=200),
]


class PortfolioCreate(BaseModel):
    """Fields accepted when a portfolio is created."""

    model_config = ConfigDict(extra="forbid")

    name: ResourceName = Field(examples=["Long-term capital"])
    base_currency: CurrencyCode = Field(examples=["USD"])


class PortfolioUpdate(BaseModel):
    """Mutable portfolio fields."""

    model_config = ConfigDict(extra="forbid")

    name: ResourceName | None = None
    base_currency: CurrencyCode | None = None


class PortfolioResponse(BaseModel):
    """Public portfolio representation."""

    model_config = ConfigDict(from_attributes=True, extra="forbid")

    id: UUID
    name: str
    base_currency: CurrencyCode
    created_at: AwareDateTime
    updated_at: AwareDateTime

class PortfolioListResponse(BaseModel):
    """Bounded portfolio collection."""

    items: list[PortfolioResponse]
    limit: int
    offset: int
