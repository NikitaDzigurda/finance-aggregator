from __future__ import annotations

from typing import Annotated, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, StringConstraints

from allocation.models import AssetClass
from shared.exact import AwareDateTime

CategoryName = Annotated[
    str,
    StringConstraints(strict=True, strip_whitespace=True, min_length=1, max_length=100),
]


class AllocationCategoryCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: CategoryName
    system_class: AssetClass


class AllocationCategoryUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: CategoryName | None = None
    system_class: AssetClass | None = None


class AllocationCategoryResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True, extra="forbid")

    id: UUID
    portfolio_id: UUID | None
    name: str
    system_class: AssetClass
    is_system: bool
    created_at: AwareDateTime
    updated_at: AwareDateTime


class AllocationCategoryListResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    items: list[AllocationCategoryResponse]


class InstrumentCategoryAssignmentRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    category_id: UUID


class InstrumentCategoryAssignmentResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    portfolio_id: UUID
    instrument_id: UUID
    source: Literal["default", "override"]
    category: AllocationCategoryResponse
