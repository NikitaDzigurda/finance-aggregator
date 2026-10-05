from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass
from decimal import ROUND_HALF_EVEN, Context, Decimal, localcontext
from enum import StrEnum
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from shared.exact import MONEY_SPEC, AwareDateTime, CurrencyCode, Money, validate_decimal

_ANALYTICS_CONTEXT = Context(prec=100, rounding=ROUND_HALF_EVEN)
_MONEY_QUANTUM = Decimal("0.000000000000000001")


class ReportingCurrency(StrEnum):
    RUB = "RUB"
    USD = "USD"


class DataQualityState(StrEnum):
    COMPLETE = "complete"
    PARTIAL = "partial"
    UNAVAILABLE = "unavailable"


class CurrentMetric(StrEnum):
    CURRENT_VALUE = "current_value"
    COST_BASIS = "cost_basis"
    REALISED_PNL = "realised_pnl"
    UNREALISED_PNL = "unrealised_pnl"
    INCOME = "income"
    FEES = "fees"
    TAXES = "taxes"
    DEPOSITS = "deposits"
    WITHDRAWALS = "withdrawals"


@dataclass(frozen=True, slots=True)
class MetricComponent:
    metric: CurrentMetric
    amount: Decimal | None
    currency: str | None


@dataclass(frozen=True, slots=True)
class MetricDiagnostic:
    severity: Literal["warning", "error"]
    code: Literal["metric_input_unavailable", "fx_rate_missing"]
    message: str


@dataclass(frozen=True, slots=True)
class MetricAggregation:
    metric: CurrentMetric
    reporting_currency: str
    quality: DataQualityState
    total_value: Decimal | None
    known_value: Decimal
    included_components: int
    excluded_components: int
    diagnostics: tuple[MetricDiagnostic, ...]


type CurrencyConverter = Callable[[Decimal, str, str], Decimal | None]


def aggregate_current_metric(
    metric: CurrentMetric,
    components: Sequence[MetricComponent],
    *,
    reporting_currency: str,
    converter: CurrencyConverter,
) -> MetricAggregation:
    """Aggregate one independent metric without leaking missingness into other metrics."""
    known_value = Decimal(0)
    included = 0
    excluded = 0
    diagnostics: list[MetricDiagnostic] = []
    for component in components:
        if component.metric is not metric:
            continue
        if component.amount is None or component.currency is None:
            excluded += 1
            diagnostics.append(
                MetricDiagnostic(
                    severity="warning",
                    code="metric_input_unavailable",
                    message="A dependent metric component is unavailable",
                )
            )
            continue
        converted = (
            component.amount
            if component.currency == reporting_currency
            else converter(component.amount, component.currency, reporting_currency)
        )
        if converted is None:
            excluded += 1
            diagnostics.append(
                MetricDiagnostic(
                    severity="warning",
                    code="fx_rate_missing",
                    message="A required exchange rate is unavailable",
                )
            )
            continue
        with localcontext(_ANALYTICS_CONTEXT):
            converted_money = validate_decimal(
                converted.quantize(_MONEY_QUANTUM, rounding=ROUND_HALF_EVEN),
                MONEY_SPEC,
            )
            known_value = validate_decimal(known_value + converted_money, MONEY_SPEC)
        included += 1

    if excluded == 0:
        quality = DataQualityState.COMPLETE
        total_value: Decimal | None = known_value
    elif included:
        quality = DataQualityState.PARTIAL
        total_value = None
    else:
        quality = DataQualityState.UNAVAILABLE
        total_value = None
    return MetricAggregation(
        metric=metric,
        reporting_currency=reporting_currency,
        quality=quality,
        total_value=total_value,
        known_value=known_value,
        included_components=included,
        excluded_components=excluded,
        diagnostics=tuple(diagnostics),
    )


class MetricDiagnosticResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    severity: Literal["warning", "error"]
    code: Literal["metric_input_unavailable", "fx_rate_missing"]
    message: str


class CurrentMetricResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    metric: CurrentMetric
    valuation_as_of: AwareDateTime
    reporting_currency: CurrencyCode
    quality: DataQualityState
    total_value: Money | None
    known_value: Money
    included_components: int = Field(ge=0)
    excluded_components: int = Field(ge=0)
    diagnostics: list[MetricDiagnosticResponse]


def metric_response(
    aggregation: MetricAggregation,
    *,
    valuation_as_of: AwareDateTime,
) -> CurrentMetricResponse:
    return CurrentMetricResponse(
        metric=aggregation.metric,
        valuation_as_of=valuation_as_of,
        reporting_currency=aggregation.reporting_currency,
        quality=aggregation.quality,
        total_value=aggregation.total_value,
        known_value=aggregation.known_value,
        included_components=aggregation.included_components,
        excluded_components=aggregation.excluded_components,
        diagnostics=[
            MetricDiagnosticResponse(
                severity=item.severity,
                code=item.code,
                message=item.message,
            )
            for item in aggregation.diagnostics
        ],
    )
