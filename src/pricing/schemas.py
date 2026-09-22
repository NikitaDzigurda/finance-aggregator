from __future__ import annotations

from decimal import Decimal
from typing import Annotated, Literal
from uuid import UUID

from pydantic import AfterValidator, BaseModel, ConfigDict, Field, model_validator
from pydantic_core import PydanticCustomError

from imports.models import ImportJobStatus
from pricing.models import ExchangeRateMode, MarketMappingStatus, MarketPriceUnit
from shared.exact import AwareDateTime, CurrencyCode, Price, Rate


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
    source: Literal["manual", "automatic"]
    provider: str
    price_kind: str
    fetched_at: AwareDateTime
    time_quality: Literal["user_supplied", "provider_snapshot", "provider_trade"]
    created_at: AwareDateTime
    updated_at: AwareDateTime


class MarketSyncCountersResponse(BaseModel):
    inserted: int = Field(ge=0)
    repeated: int = Field(ge=0)
    skipped_unmapped: int = Field(ge=0)
    skipped_invalid: int = Field(ge=0)
    recalculated_portfolios: int = Field(ge=0)
    failed_portfolios: int = Field(ge=0)


class MarketSyncJobResponse(BaseModel):
    id: UUID
    provider: str
    market: str
    status: ImportJobStatus
    attempts: int = Field(ge=0)
    created_at: AwareDateTime
    updated_at: AwareDateTime
    error_code: str | None
    counters: MarketSyncCountersResponse | None


class MarketMappingResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True, extra="forbid")

    id: UUID
    instrument_id: UUID
    provider: str
    market: str
    symbol: str
    quote_currency: CurrencyCode
    identifier_type: Literal["isin", "crypto_asset_code"]
    identifier_value: str
    price_unit: MarketPriceUnit
    price_kind: str
    time_quality: Literal["provider_snapshot", "provider_trade"]
    status: MarketMappingStatus
    confirmation_source: str | None
    confirmed_at: AwareDateTime | None
    reason_code: str | None
    eligible: bool


class MarketMappingListResponse(BaseModel):
    instrument_id: UUID
    status: Literal["verified", "ambiguous", "unsupported", "unmapped"]
    items: list[MarketMappingResponse]


class MarketPriceListResponse(BaseModel):
    items: list[MarketPriceResponse]
    limit: int
    offset: int


class MarketPriceBatchCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    items: list[MarketPriceCreate] = Field(min_length=1, max_length=200)

    @model_validator(mode="after")
    def unique_observations(self) -> MarketPriceBatchCreate:
        identities = [(item.instrument_id, item.currency, item.observed_at) for item in self.items]
        if len(identities) != len(set(identities)):
            raise PydanticCustomError(
                "market_price_batch_duplicate",
                "Batch contains duplicate price observations",
            )
        return self


class MarketPriceBatchResponse(BaseModel):
    items: list[MarketPriceResponse]


class MarketPriceDiagnosticResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    severity: Literal["warning", "error"]
    code: Literal["market_price_missing", "market_price_stale"]
    message: str


class MarketPriceResolveResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    instrument_id: UUID
    valuation_as_of: AwareDateTime
    status: Literal["fresh", "stale", "unavailable"]
    observation: MarketPriceResponse | None
    age_seconds: int | None = Field(default=None, ge=0)
    diagnostics: list[MarketPriceDiagnosticResponse]


class ExchangeRateCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    base_currency: CurrencyCode
    quote_currency: CurrencyCode
    rate: Annotated[Rate, AfterValidator(_positive)]
    observed_at: AwareDateTime

    @model_validator(mode="after")
    def different_currencies(self) -> ExchangeRateCreate:
        if self.base_currency == self.quote_currency:
            raise PydanticCustomError(
                "exchange_rate_same_currency",
                "Exchange-rate currencies must be different",
            )
        return self


class ExchangeRateBatchCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    items: list[ExchangeRateCreate] = Field(min_length=1, max_length=200)

    @model_validator(mode="after")
    def unique_observations(self) -> ExchangeRateBatchCreate:
        identities = [
            (item.base_currency, item.quote_currency, item.observed_at) for item in self.items
        ]
        if len(identities) != len(set(identities)):
            raise PydanticCustomError(
                "exchange_rate_batch_duplicate",
                "Batch contains duplicate exchange-rate observations",
            )
        return self


class ExchangeRateResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True, extra="forbid")

    id: UUID
    base_currency: CurrencyCode
    quote_currency: CurrencyCode
    rate: Rate
    observed_at: AwareDateTime
    fetched_at: AwareDateTime
    provider: str
    source: str
    mode: ExchangeRateMode
    created_at: AwareDateTime
    updated_at: AwareDateTime


class ExchangeRateBatchResponse(BaseModel):
    items: list[ExchangeRateResponse]


class ExchangeRateListResponse(BaseModel):
    items: list[ExchangeRateResponse]
    limit: int
    offset: int


class FxRateUseResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    exchange_rate_id: UUID
    base_currency: CurrencyCode
    quote_currency: CurrencyCode
    rate: Rate
    observed_at: AwareDateTime
    age_seconds: int = Field(ge=0)
    provider: str
    source: str
    mode: ExchangeRateMode


class FxRateDiagnosticResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    severity: Literal["warning", "error"]
    code: Literal["fx_rate_missing", "fx_rate_stale"]
    message: str


class FxRateResolveResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    base_currency: CurrencyCode
    quote_currency: CurrencyCode
    valuation_as_of: AwareDateTime
    status: Literal["fresh", "stale", "unavailable"]
    rate: Rate | None
    path: Literal["identity", "direct", "inverse", "pivot", "unavailable"]
    observations: list[FxRateUseResponse]
    diagnostics: list[FxRateDiagnosticResponse]


class FxSyncJobResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: UUID
    status: ImportJobStatus
    attempts: int = Field(ge=0)
    available_at: AwareDateTime
    created_at: AwareDateTime
    updated_at: AwareDateTime
    duplicate: bool = False
    error_code: str | None = None
