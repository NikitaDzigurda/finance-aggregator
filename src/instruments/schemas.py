from __future__ import annotations

import re
from typing import Annotated, Self
from uuid import UUID

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    StringConstraints,
    model_validator,
)
from pydantic_core import PydanticCustomError

from instruments.models import InstrumentIdentifierType, InstrumentType
from portfolios.schemas import ResourceName
from shared.exact import AwareDateTime, CurrencyCode

type IdentifierValue = Annotated[
    str,
    StringConstraints(strict=True, strip_whitespace=True, min_length=1, max_length=128),
]
type IdentifierScope = Annotated[
    str,
    StringConstraints(strict=True, strip_whitespace=True, min_length=1, max_length=64),
]

_ISIN_RE = re.compile(r"^[A-Z]{2}[A-Z0-9]{9}[0-9]$")
_MARKET_CODE_RE = re.compile(r"^[A-Z0-9][A-Z0-9._-]{0,31}$")
_PROVIDER_RE = re.compile(r"^[a-z0-9][a-z0-9._-]{0,63}$")


class InstrumentIdentifierCreate(BaseModel):
    """Typed identifier and the scope needed to make it stable."""

    model_config = ConfigDict(extra="forbid")

    identifier_type: InstrumentIdentifierType
    value: IdentifierValue
    exchange: IdentifierScope | None = None
    provider: IdentifierScope | None = None

    @model_validator(mode="after")
    def validate_identifier_scope(self) -> Self:
        if self.identifier_type is InstrumentIdentifierType.TICKER:
            if self.exchange is None:
                raise PydanticCustomError(
                    "identifier_exchange_required",
                    "Ticker identifiers require an exchange",
                )
            if self.provider is not None:
                raise PydanticCustomError(
                    "identifier_scope_invalid",
                    "Ticker identifiers cannot include a provider",
                )
            if _MARKET_CODE_RE.fullmatch(self.value) is None:
                raise PydanticCustomError(
                    "identifier_format",
                    "Ticker must use uppercase market-code notation",
                )
            if _MARKET_CODE_RE.fullmatch(self.exchange) is None:
                raise PydanticCustomError(
                    "identifier_exchange_format",
                    "Exchange must use uppercase market-code notation",
                )
        elif self.identifier_type is InstrumentIdentifierType.PROVIDER_CODE:
            if self.provider is None:
                raise PydanticCustomError(
                    "identifier_provider_required",
                    "Provider code identifiers require a provider",
                )
            if self.exchange is not None:
                raise PydanticCustomError(
                    "identifier_scope_invalid",
                    "Provider code identifiers cannot include an exchange",
                )
            if _PROVIDER_RE.fullmatch(self.provider) is None:
                raise PydanticCustomError(
                    "identifier_provider_format",
                    "Provider must use lowercase stable-code notation",
                )
        else:
            if self.exchange is not None or self.provider is not None:
                raise PydanticCustomError(
                    "identifier_scope_invalid",
                    "ISIN and crypto asset codes cannot include exchange or provider scope",
                )
            if self.identifier_type is InstrumentIdentifierType.ISIN:
                if _ISIN_RE.fullmatch(self.value) is None:
                    raise PydanticCustomError(
                        "identifier_format",
                        "ISIN must contain 12 uppercase alphanumeric characters",
                    )
            elif _MARKET_CODE_RE.fullmatch(self.value) is None:
                raise PydanticCustomError(
                    "identifier_format",
                    "Crypto asset code must use uppercase market-code notation",
                )
        return self

    def uniqueness_key(self) -> tuple[str, str, str | None, str | None]:
        return (self.identifier_type.value, self.value, self.exchange, self.provider)


class InstrumentIdentifierResponse(InstrumentIdentifierCreate):
    """Stored typed identifier."""

    model_config = ConfigDict(from_attributes=True, extra="forbid")

    id: UUID


class InstrumentCreate(BaseModel):
    """Fields accepted when a canonical instrument is created."""

    model_config = ConfigDict(extra="forbid")

    name: ResourceName
    instrument_type: InstrumentType
    currency: CurrencyCode
    identifiers: list[InstrumentIdentifierCreate] = Field(min_length=1, max_length=20)

    @model_validator(mode="after")
    def reject_duplicate_identifiers(self) -> Self:
        keys = [identifier.uniqueness_key() for identifier in self.identifiers]
        if len(keys) != len(set(keys)):
            raise PydanticCustomError(
                "duplicate_identifier",
                "Instrument identifiers must be unique within the request",
            )
        return self


class InstrumentUpdate(BaseModel):
    """Mutable instrument fields; identifiers are replaced atomically when supplied."""

    model_config = ConfigDict(extra="forbid")

    name: ResourceName | None = None
    instrument_type: InstrumentType | None = None
    currency: CurrencyCode | None = None
    identifiers: list[InstrumentIdentifierCreate] | None = Field(
        default=None,
        min_length=1,
        max_length=20,
    )

    @model_validator(mode="after")
    def reject_duplicate_identifiers(self) -> Self:
        if self.identifiers is None:
            return self
        keys = [identifier.uniqueness_key() for identifier in self.identifiers]
        if len(keys) != len(set(keys)):
            raise PydanticCustomError(
                "duplicate_identifier",
                "Instrument identifiers must be unique within the request",
            )
        return self


class InstrumentResponse(BaseModel):
    """Public canonical instrument representation."""

    model_config = ConfigDict(from_attributes=True, extra="forbid")

    id: UUID
    name: str
    instrument_type: InstrumentType
    currency: CurrencyCode
    identifiers: list[InstrumentIdentifierResponse]
    created_at: AwareDateTime
    updated_at: AwareDateTime


class InstrumentListResponse(BaseModel):
    """Bounded instrument collection."""

    items: list[InstrumentResponse]
    limit: int
    offset: int
