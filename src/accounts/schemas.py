from __future__ import annotations

from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from accounts.models import AccountType
from portfolios.schemas import ResourceName
from shared.exact import AwareDateTime


class AccountCreate(BaseModel):
    """Fields accepted for a portfolio-owned account."""

    model_config = ConfigDict(extra="forbid")

    name: ResourceName = Field(examples=["Primary broker"])
    account_type: AccountType
    institution_name: ResourceName | None = Field(default=None, examples=["Example Broker"])


class AccountUpdate(BaseModel):
    """Mutable account fields; portfolio ownership is immutable."""

    model_config = ConfigDict(extra="forbid")

    name: ResourceName | None = None
    account_type: AccountType | None = None
    institution_name: ResourceName | None = None


class AccountResponse(BaseModel):
    """Public account representation."""

    model_config = ConfigDict(from_attributes=True, extra="forbid")

    id: UUID
    portfolio_id: UUID
    name: str
    account_type: AccountType
    institution_name: str | None
    created_at: AwareDateTime
    updated_at: AwareDateTime


class AccountListResponse(BaseModel):
    """Bounded account collection."""

    items: list[AccountResponse]
    limit: int
    offset: int
