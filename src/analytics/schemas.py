from __future__ import annotations

from datetime import date
from enum import StrEnum
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from accounts.models import AccountType
from allocation.schemas import AllocationCategoryResponse
from analytics.contracts import CurrentMetricResponse, DataQualityState
from instruments.models import InstrumentType
from pricing.schemas import MarketPriceResponse
from shared.exact import AwareDateTime, CurrencyCode, Money, Quantity, Rate


class AnalyticsDiagnosticResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    severity: Literal["warning", "error"]
    code: str
    message: str


class AnalyticsOverviewResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    portfolio_id: UUID
    valuation_as_of: AwareDateTime
    reporting_currency: CurrencyCode
    snapshot_as_of: AwareDateTime | None
    snapshot_fresh: bool
    ledger_operation_count: int = Field(ge=0)
    diagnostics: list[AnalyticsDiagnosticResponse]
    current_value: CurrentMetricResponse
    cost_basis: CurrentMetricResponse
    realised_pnl: CurrentMetricResponse
    unrealised_pnl: CurrentMetricResponse
    income: CurrentMetricResponse
    fees: CurrentMetricResponse
    taxes: CurrentMetricResponse


class HoldingKind(StrEnum):
    INSTRUMENT = "instrument"
    CASH = "cash"


class AnalyticsHoldingResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    kind: HoldingKind
    account_id: UUID
    account_name: str
    account_type: AccountType
    institution_name: str | None
    instrument_id: UUID | None
    instrument_name: str | None
    instrument_type: InstrumentType | None
    category: AllocationCategoryResponse
    quantity: Quantity | None
    source_currency: CurrencyCode
    reporting_currency: CurrencyCode
    cost_basis: Money | None
    market_value: Money | None
    realised_pnl: Money | None
    unrealised_pnl: Money | None
    price_observation: MarketPriceResponse | None = None
    quality: DataQualityState
    diagnostics: list[AnalyticsDiagnosticResponse]


class AnalyticsHoldingsResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    portfolio_id: UUID
    valuation_as_of: AwareDateTime
    reporting_currency: CurrencyCode
    snapshot_fresh: bool
    current_value: CurrentMetricResponse
    items: list[AnalyticsHoldingResponse]
    diagnostics: list[AnalyticsDiagnosticResponse]


class BreakdownDimension(StrEnum):
    ACCOUNT = "account"
    INSTITUTION = "institution"
    CURRENCY = "currency"
    INSTRUMENT_TYPE = "instrument_type"
    ALLOCATION_CATEGORY = "allocation_category"


class BreakdownItemResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    key: str
    label: str
    known_value: Money
    weight: Rate | None


class BreakdownDimensionResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    dimension: BreakdownDimension
    quality: DataQualityState
    denominator: Money
    included_components: int = Field(ge=0)
    excluded_components: int = Field(ge=0)
    items: list[BreakdownItemResponse]


class AnalyticsBreakdownResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    portfolio_id: UUID
    valuation_as_of: AwareDateTime
    reporting_currency: CurrencyCode
    snapshot_fresh: bool
    dimensions: list[BreakdownDimensionResponse]
    diagnostics: list[AnalyticsDiagnosticResponse]


class CounterpartyExposureItemResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    key: str
    institution_name: str
    account_types: list[AccountType]
    account_count: int = Field(ge=1)
    known_value: Money
    weight: Rate | None


class CounterpartyExposureResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    portfolio_id: UUID
    valuation_as_of: AwareDateTime
    reporting_currency: CurrencyCode
    snapshot_fresh: bool
    quality: DataQualityState
    denominator: Money
    included_components: int = Field(ge=0)
    excluded_components: int = Field(ge=0)
    items: list[CounterpartyExposureItemResponse]
    diagnostics: list[AnalyticsDiagnosticResponse]


class ActualAllocationItemResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    category: AllocationCategoryResponse
    known_value: Money
    actual_weight: Rate | None


class ActualAllocationResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    portfolio_id: UUID
    valuation_as_of: AwareDateTime
    reporting_currency: CurrencyCode
    snapshot_fresh: bool
    quality: DataQualityState
    denominator: Money
    included_components: int = Field(ge=0)
    excluded_components: int = Field(ge=0)
    items: list[ActualAllocationItemResponse]
    diagnostics: list[AnalyticsDiagnosticResponse]


class TimelineBucket(StrEnum):
    DAY = "day"
    WEEK = "week"
    MONTH = "month"


class TimelineUnitType(StrEnum):
    CURRENCY = "currency"
    ASSET = "asset"


class TimelineMetric(StrEnum):
    DEPOSITS = "deposits"
    WITHDRAWALS = "withdrawals"
    INCOME = "income"
    FEES = "fees"
    TAXES = "taxes"
    TRADE_TURNOVER = "trade_turnover"
    REALISED_PNL = "realised_pnl"


class TimelineBucketValueResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    bucket_start: date
    value: Money


class TimelineSeriesResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    key: str = Field(
        description=(
            "Stable series key formatted as '<metric>:<unit_type>:<unit>'; use it "
            "for frontend identity instead of a translated label"
        )
    )
    metric: TimelineMetric
    unit_type: TimelineUnitType
    unit: str
    buckets: list[TimelineBucketValueResponse]


class TimelineProvenanceResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    calculation_contract_version: int = Field(ge=1)
    operation_effects_contract_version: int = Field(ge=1)
    calculation_as_of: AwareDateTime
    snapshot_as_of: AwareDateTime | None
    snapshot_fresh: bool


class EventTimelineResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    portfolio_id: UUID
    period_from: date
    period_to: date
    bucket: TimelineBucket
    timezone: str
    historical_reporting_conversion: Literal["unavailable"] = "unavailable"
    series_total: int = Field(
        ge=0,
        description="Total number of series after filters and before pagination",
    )
    limit: int = Field(ge=1, le=100, description="Applied series page size")
    offset: int = Field(ge=0, description="Applied zero-based series offset")
    series: list[TimelineSeriesResponse]
    diagnostics: list[AnalyticsDiagnosticResponse]
    provenance: TimelineProvenanceResponse


class DataQualityImpactResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    endpoint: str
    metric: str
    state: DataQualityState


class DataQualityDiagnosticSummaryResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    severity: Literal["warning", "error"]
    code: str
    message: str
    count: int = Field(ge=1)
    impacts: list[DataQualityImpactResponse]


class DataQualityProvenanceResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    calculation_contract_version: int = Field(ge=1)
    snapshot_as_of: AwareDateTime | None
    snapshot_fresh: bool
    used_market_price_observation_count: int = Field(ge=0)
    used_market_price_observed_at: list[AwareDateTime]
    used_fx_observation_count: int = Field(ge=0)
    used_fx_observed_at: list[AwareDateTime]


class AnalyticsDataQualityResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    portfolio_id: UUID
    valuation_as_of: AwareDateTime
    reporting_currency: CurrencyCode
    diagnostics: list[DataQualityDiagnosticSummaryResponse]
    provenance: DataQualityProvenanceResponse
