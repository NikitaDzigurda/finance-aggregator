from datetime import UTC, datetime
from decimal import Decimal
from uuid import uuid4

from analytics.contracts import (
    CurrentMetric,
    DataQualityState,
    MetricComponent,
    aggregate_current_metric,
    metric_response,
)
from pricing.fx import FxRateCandidate, resolve_fx_rate
from pricing.models import ExchangeRateMode


def test_metrics_keep_flows_and_profit_separate_and_serialize_exactly() -> None:
    components = [
        MetricComponent(CurrentMetric.DEPOSITS, Decimal("1000.25"), "USD"),
        MetricComponent(CurrentMetric.WITHDRAWALS, Decimal("125.10"), "USD"),
        MetricComponent(CurrentMetric.INCOME, Decimal("12.345"), "USD"),
        MetricComponent(CurrentMetric.REALISED_PNL, Decimal("-3.5"), "USD"),
    ]
    def converter(amount: Decimal, _source: str, _target: str) -> Decimal:
        return amount

    deposits = aggregate_current_metric(
        CurrentMetric.DEPOSITS,
        components,
        reporting_currency="USD",
        converter=converter,
    )
    income = aggregate_current_metric(
        CurrentMetric.INCOME,
        components,
        reporting_currency="USD",
        converter=converter,
    )
    realised = aggregate_current_metric(
        CurrentMetric.REALISED_PNL,
        components,
        reporting_currency="USD",
        converter=converter,
    )

    assert deposits.total_value == Decimal("1000.25")
    assert income.total_value == Decimal("12.345")
    assert realised.total_value == Decimal("-3.5")
    body = metric_response(
        income,
        valuation_as_of=datetime(2026, 8, 22, tzinfo=UTC),
    ).model_dump(mode="json")
    assert body["total_value"] == "12.345000000000000000"
    assert body["known_value"] == "12.345000000000000000"


def test_missing_fx_and_unknown_basis_affect_only_dependent_metric() -> None:
    value_components = [
        MetricComponent(CurrentMetric.CURRENT_VALUE, Decimal("100"), "USD"),
        MetricComponent(CurrentMetric.CURRENT_VALUE, Decimal("50"), "EUR"),
    ]
    basis_components = [
        MetricComponent(CurrentMetric.COST_BASIS, None, None),
    ]

    value = aggregate_current_metric(
        CurrentMetric.CURRENT_VALUE,
        value_components,
        reporting_currency="USD",
        converter=lambda _amount, _source, _target: None,
    )
    basis = aggregate_current_metric(
        CurrentMetric.COST_BASIS,
        basis_components,
        reporting_currency="USD",
        converter=lambda _amount, _source, _target: None,
    )
    income = aggregate_current_metric(
        CurrentMetric.INCOME,
        value_components + basis_components,
        reporting_currency="USD",
        converter=lambda _amount, _source, _target: None,
    )

    assert value.quality is DataQualityState.PARTIAL
    assert value.total_value is None
    assert value.known_value == Decimal("100")
    assert value.excluded_components == 1
    assert value.diagnostics[0].code == "fx_rate_missing"
    assert basis.quality is DataQualityState.UNAVAILABLE
    assert basis.total_value is None
    assert basis.known_value == Decimal("0")
    assert income.quality is DataQualityState.COMPLETE
    assert income.total_value == Decimal("0")

    missing_price = aggregate_current_metric(
        CurrentMetric.CURRENT_VALUE,
        [
            MetricComponent(CurrentMetric.CURRENT_VALUE, Decimal("25"), "USD"),
            MetricComponent(CurrentMetric.CURRENT_VALUE, None, "USD"),
        ],
        reporting_currency="USD",
        converter=lambda amount, _source, _target: amount,
    )
    assert missing_price.quality is DataQualityState.PARTIAL
    assert missing_price.known_value == Decimal("25.000000000000000000")
    assert missing_price.excluded_components == 1


def test_same_components_consolidate_consistently_in_rub_and_usd() -> None:
    observed_at = datetime(2026, 8, 21, tzinfo=UTC)
    as_of = datetime(2026, 8, 22, tzinfo=UTC)
    rates = [
        FxRateCandidate(
            id=uuid4(),
            base_currency="USD",
            quote_currency="RUB",
            rate=Decimal("80"),
            observed_at=observed_at,
            provider="manual",
            source="test",
            mode=ExchangeRateMode.MANUAL,
        )
    ]
    components = [
        MetricComponent(CurrentMetric.CURRENT_VALUE, Decimal("10"), "USD"),
        MetricComponent(CurrentMetric.CURRENT_VALUE, Decimal("800"), "RUB"),
    ]

    def converter(amount: Decimal, source: str, target: str) -> Decimal | None:
        resolved = resolve_fx_rate(
            rates,
            base_currency=source,
            quote_currency=target,
            valuation_as_of=as_of,
            stale_after_seconds=3 * 24 * 60 * 60,
        )
        return None if resolved.rate is None else amount * resolved.rate

    rub = aggregate_current_metric(
        CurrentMetric.CURRENT_VALUE,
        components,
        reporting_currency="RUB",
        converter=converter,
    )
    usd = aggregate_current_metric(
        CurrentMetric.CURRENT_VALUE,
        components,
        reporting_currency="USD",
        converter=converter,
    )

    assert rub.total_value == Decimal("1600")
    assert usd.total_value == Decimal("20.000000000000000000")
    assert rub.quality is DataQualityState.COMPLETE
    assert usd.quality is DataQualityState.COMPLETE
