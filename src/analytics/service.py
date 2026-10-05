from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import ROUND_HALF_EVEN, Context, Decimal, localcontext
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from accounts.models import AccountModel, AccountType
from accounts.service import list_portfolio_accounts
from allocation.calculation import (
    AllocationValuationComponent,
    calculate_actual_allocation,
)
from allocation.models import AllocationCategoryModel, AssetClass
from allocation.schemas import AllocationCategoryResponse
from allocation.service import (
    SYSTEM_CATEGORY_IDS,
    get_visible_category,
    list_categories,
    resolve_categories_for_instruments,
)
from analytics.contracts import (
    CurrencyConverter,
    CurrentMetric,
    CurrentMetricResponse,
    DataQualityState,
    MetricComponent,
    ReportingCurrency,
    aggregate_current_metric,
    metric_response,
)
from analytics.schemas import (
    ActualAllocationItemResponse,
    ActualAllocationResponse,
    AnalyticsBreakdownResponse,
    AnalyticsDiagnosticResponse,
    AnalyticsHoldingResponse,
    AnalyticsHoldingsResponse,
    AnalyticsOverviewResponse,
    BreakdownDimension,
    BreakdownDimensionResponse,
    BreakdownItemResponse,
    CounterpartyExposureItemResponse,
    CounterpartyExposureResponse,
    HoldingKind,
)
from calculation.models import (
    CalculatedCashBalanceModel,
    CalculatedCurrencyMetricsModel,
    CalculatedPositionModel,
    CalculationSnapshotModel,
)
from calculation.service import get_position_snapshot
from instruments.models import InstrumentModel
from instruments.service import get_instruments_by_ids
from operations.service import count_portfolio_operations
from portfolios.models import PortfolioModel
from portfolios.service import get_portfolio
from pricing.fx import ResolvedFxRate
from pricing.schemas import MarketPriceResponse
from pricing.service import (
    applicable_market_price,
    get_market_prices_by_ids,
    latest_market_prices_for_instruments,
    market_price_stale_after_seconds,
    resolve_exchange_rate,
)
from shared.config import Settings
from shared.errors import ApiErrorException
from shared.exact import MONEY_SPEC, RATE_SPEC, validate_decimal

_ANALYTICS_CONTEXT = Context(prec=100, rounding=ROUND_HALF_EVEN)
_MONEY_QUANTUM = Decimal("0.000000000000000001")
_RATE_QUANTUM = Decimal("0.000000000000000000000001")


@dataclass(slots=True)
class HoldingProjection:
    response: AnalyticsHoldingResponse
    category_id: UUID
    account_id: UUID
    institution_key: str
    institution_label: str
    breakdown_keys: dict[BreakdownDimension, tuple[str, str]]


@dataclass(slots=True)
class AnalyticsReadModel:
    portfolio: PortfolioModel
    snapshot: CalculationSnapshotModel | None
    valuation_as_of: datetime
    reporting_currency: str
    snapshot_fresh: bool
    ledger_operation_count: int
    diagnostics: list[AnalyticsDiagnosticResponse]
    metrics: dict[CurrentMetric, CurrentMetricResponse]
    holdings: list[HoldingProjection]
    categories: dict[UUID, AllocationCategoryModel]
    market_price_observation_times: tuple[datetime, ...]
    fx_observation_times: tuple[datetime, ...]


async def build_analytics_read_model(
    session: AsyncSession,
    *,
    portfolio_id: UUID,
    reporting_currency: ReportingCurrency | None,
    settings: Settings,
) -> AnalyticsReadModel:
    portfolio = await get_portfolio(session, portfolio_id)
    if portfolio is None:
        raise ApiErrorException(
            status_code=404,
            code="portfolio_not_found",
            message="Portfolio was not found",
        )
    diagnostics: list[AnalyticsDiagnosticResponse] = []
    selected_currency = _select_reporting_currency(
        portfolio,
        reporting_currency,
        diagnostics,
    )
    snapshot = await get_position_snapshot(session, portfolio_id)
    ledger_count = await count_portfolio_operations(session, portfolio_id=portfolio_id)
    valuation_as_of = snapshot.as_of if snapshot is not None else datetime.now(UTC)
    ledger_fresh = (
        ledger_count == 0 if snapshot is None else ledger_count == snapshot.operation_count
    )
    positions_for_price_check = snapshot.positions if snapshot is not None else []
    current_prices = await latest_market_prices_for_instruments(
        session,
        instrument_ids={item.instrument_id for item in positions_for_price_check},
        valuation_as_of=max(datetime.now(UTC), valuation_as_of),
    )
    pricing_fresh = all(
        (
            selected.id
            if (
                selected := applicable_market_price(
                    current_prices,
                    instrument_id=item.instrument_id,
                    cost_currency=item.cost_currency,
                )
            )
            is not None
            else None
        )
        == item.market_price_observation_id
        for item in positions_for_price_check
    )
    snapshot_fresh = ledger_fresh and pricing_fresh
    if snapshot is None and ledger_count:
        diagnostics.append(
            _diagnostic(
                "error",
                "calculation_snapshot_missing",
                "A calculation snapshot is required for the current Ledger",
            )
        )
    elif snapshot is not None and not snapshot_fresh:
        diagnostics.append(
            _diagnostic(
                "warning",
                "calculation_snapshot_stale",
                "The saved valuation does not include the latest Ledger or price observation",
            )
        )
    if snapshot is not None:
        diagnostics.extend(_calculation_diagnostics(snapshot.diagnostics))

    accounts = {
        item.id: item
        for item in await list_portfolio_accounts(session, portfolio_id=portfolio_id)
        if item.account_type in {AccountType.BROKER, AccountType.CEX}
    }
    positions = (
        [item for item in snapshot.positions if item.account_id in accounts]
        if snapshot is not None
        else []
    )
    snapshot_prices = await get_market_prices_by_ids(
        session,
        {
            item.market_price_observation_id
            for item in positions
            if item.market_price_observation_id is not None
        },
    )
    cash_balances = (
        [item for item in snapshot.cash_balances if item.account_id in accounts]
        if snapshot is not None
        else []
    )
    currency_metrics = (
        [item for item in snapshot.currency_metrics if item.account_id in accounts]
        if snapshot is not None
        else []
    )
    instruments = {
        item.id: item
        for item in await get_instruments_by_ids(
            session,
            {item.instrument_id for item in positions},
        )
    }
    category_by_instrument = await resolve_categories_for_instruments(
        session,
        portfolio_id=portfolio_id,
        instruments=list(instruments.values()),
    )
    categories = {
        item.id: item for item in await list_categories(session, portfolio_id=portfolio_id)
    }
    cash_category = await get_visible_category(
        session,
        portfolio_id=portfolio_id,
        category_id=SYSTEM_CATEGORY_IDS[AssetClass.CASH],
    )
    if cash_category is None:
        raise RuntimeError("System cash category is not initialized")

    source_currencies = {
        currency
        for currency in [
            *(item.valuation_currency for item in positions),
            *(item.cost_currency for item in positions),
            *(item.currency for item in cash_balances),
            *(item.currency for item in currency_metrics),
        ]
        if currency is not None
    }
    rates = await _resolve_rates(
        session,
        source_currencies=source_currencies,
        reporting_currency=selected_currency,
        valuation_as_of=valuation_as_of,
        settings=settings,
    )
    diagnostics.extend(_rate_diagnostics(rates))
    converter = _converter(rates)

    holdings: list[HoldingProjection] = []
    market_price_observations: dict[UUID, datetime] = {}
    for position in positions:
        instrument = instruments[position.instrument_id]
        category = category_by_instrument[instrument.id]
        account = accounts[position.account_id]
        source_currency = position.valuation_currency or instrument.currency
        item_diagnostics = _calculation_diagnostics(position.diagnostics)
        if not snapshot_fresh:
            item_diagnostics.append(
                _diagnostic(
                    "warning",
                    "calculation_snapshot_stale",
                    "The holding is based on a stale calculation snapshot",
                )
            )
        item_diagnostics.extend(_currency_rate_diagnostics(rates, source_currency))
        price = (
            snapshot_prices.get(position.market_price_observation_id)
            if position.market_price_observation_id is not None
            else None
        )
        if (
            price is not None
            and int((max(datetime.now(UTC), valuation_as_of) - price.observed_at).total_seconds())
            > market_price_stale_after_seconds(price, settings)
        ):
            item_diagnostics.append(
                _diagnostic(
                    "warning",
                    "market_price_stale",
                    "The holding uses an old market price",
                )
            )
        if price is not None:
            market_price_observations[price.id] = price.observed_at
        market_value = _convert_money(
            position.market_value,
            source_currency if position.valuation_currency is not None else None,
            selected_currency,
            rates,
        )
        response = AnalyticsHoldingResponse(
            kind=HoldingKind.INSTRUMENT,
            account_id=account.id,
            account_name=account.name,
            account_type=account.account_type,
            institution_name=account.institution_name,
            instrument_id=instrument.id,
            instrument_name=instrument.name,
            instrument_type=instrument.instrument_type,
            category=AllocationCategoryResponse.model_validate(category),
            quantity=position.quantity,
            source_currency=source_currency,
            reporting_currency=selected_currency,
            cost_basis=_convert_money(
                position.cost_basis,
                position.cost_currency,
                selected_currency,
                rates,
            ),
            market_value=market_value,
            realised_pnl=_convert_money(
                position.realised_pnl,
                position.cost_currency,
                selected_currency,
                rates,
            ),
            unrealised_pnl=_convert_money(
                position.unrealised_pnl,
                position.valuation_currency or position.cost_currency,
                selected_currency,
                rates,
            ),
            price_observation=(
                MarketPriceResponse.model_validate(price) if price is not None else None
            ),
            quality=_holding_quality(market_value, item_diagnostics),
            diagnostics=_deduplicate_diagnostics(item_diagnostics),
        )
        holdings.append(
            _holding_projection(response, account, instrument, category, source_currency)
        )

    for cash in cash_balances:
        account = accounts[cash.account_id]
        item_diagnostics = _currency_rate_diagnostics(rates, cash.currency)
        if not snapshot_fresh:
            item_diagnostics.append(
                _diagnostic(
                    "warning",
                    "calculation_snapshot_stale",
                    "The holding is based on a stale calculation snapshot",
                )
            )
        market_value = _convert_money(
            cash.amount,
            cash.currency,
            selected_currency,
            rates,
        )
        response = AnalyticsHoldingResponse(
            kind=HoldingKind.CASH,
            account_id=account.id,
            account_name=account.name,
            account_type=account.account_type,
            institution_name=account.institution_name,
            instrument_id=None,
            instrument_name=None,
            instrument_type=None,
            category=AllocationCategoryResponse.model_validate(cash_category),
            quantity=None,
            source_currency=cash.currency,
            reporting_currency=selected_currency,
            cost_basis=None,
            market_value=market_value,
            realised_pnl=None,
            unrealised_pnl=None,
            quality=_holding_quality(market_value, item_diagnostics),
            diagnostics=_deduplicate_diagnostics(item_diagnostics),
        )
        holdings.append(_holding_projection(response, account, None, cash_category, cash.currency))

    metrics = _build_metrics(
        positions=positions,
        cash_balances=cash_balances,
        currency_metrics=currency_metrics,
        reporting_currency=selected_currency,
        valuation_as_of=valuation_as_of,
        converter=converter,
        snapshot_fresh=snapshot_fresh,
    )
    holdings.sort(
        key=lambda item: (
            str(item.response.account_id),
            item.response.kind,
            str(item.response.instrument_id or item.response.source_currency),
        )
    )
    return AnalyticsReadModel(
        portfolio=portfolio,
        snapshot=snapshot,
        valuation_as_of=valuation_as_of,
        reporting_currency=selected_currency,
        snapshot_fresh=snapshot_fresh,
        ledger_operation_count=ledger_count,
        diagnostics=_deduplicate_diagnostics(diagnostics),
        metrics=metrics,
        holdings=holdings,
        categories=categories,
        market_price_observation_times=tuple(sorted(market_price_observations.values())),
        fx_observation_times=tuple(
            sorted(
                {
                    observation.candidate.id: observation.candidate.observed_at
                    for rate in rates.values()
                    for observation in rate.observations
                }.values()
            )
        ),
    )


def overview_response(model: AnalyticsReadModel) -> AnalyticsOverviewResponse:
    return AnalyticsOverviewResponse(
        portfolio_id=model.portfolio.id,
        valuation_as_of=model.valuation_as_of,
        reporting_currency=model.reporting_currency,
        snapshot_as_of=model.snapshot.as_of if model.snapshot is not None else None,
        snapshot_fresh=model.snapshot_fresh,
        ledger_operation_count=model.ledger_operation_count,
        diagnostics=model.diagnostics,
        current_value=model.metrics[CurrentMetric.CURRENT_VALUE],
        cost_basis=model.metrics[CurrentMetric.COST_BASIS],
        realised_pnl=model.metrics[CurrentMetric.REALISED_PNL],
        unrealised_pnl=model.metrics[CurrentMetric.UNREALISED_PNL],
        income=model.metrics[CurrentMetric.INCOME],
        fees=model.metrics[CurrentMetric.FEES],
        taxes=model.metrics[CurrentMetric.TAXES],
    )


def holdings_response(model: AnalyticsReadModel) -> AnalyticsHoldingsResponse:
    return AnalyticsHoldingsResponse(
        portfolio_id=model.portfolio.id,
        valuation_as_of=model.valuation_as_of,
        reporting_currency=model.reporting_currency,
        snapshot_fresh=model.snapshot_fresh,
        current_value=model.metrics[CurrentMetric.CURRENT_VALUE],
        items=[item.response for item in model.holdings],
        diagnostics=model.diagnostics,
    )


def breakdown_response(model: AnalyticsReadModel) -> AnalyticsBreakdownResponse:
    current_value = model.metrics[CurrentMetric.CURRENT_VALUE]
    dimensions: list[BreakdownDimensionResponse] = []
    for dimension in BreakdownDimension:
        grouped: defaultdict[tuple[str, str], Decimal] = defaultdict(Decimal)
        for holding in model.holdings:
            if holding.response.market_value is None:
                continue
            grouped[holding.breakdown_keys[dimension]] += holding.response.market_value
        weights = _normalized_weights(
            {key[0]: value for key, value in grouped.items()},
            current_value.known_value,
        )
        dimensions.append(
            BreakdownDimensionResponse(
                dimension=dimension,
                quality=current_value.quality,
                denominator=current_value.known_value,
                included_components=current_value.included_components,
                excluded_components=current_value.excluded_components,
                items=[
                    BreakdownItemResponse(
                        key=key,
                        label=label,
                        known_value=value,
                        weight=weights.get(key),
                    )
                    for (key, label), value in sorted(grouped.items())
                ],
            )
        )
    return AnalyticsBreakdownResponse(
        portfolio_id=model.portfolio.id,
        valuation_as_of=model.valuation_as_of,
        reporting_currency=model.reporting_currency,
        snapshot_fresh=model.snapshot_fresh,
        dimensions=dimensions,
        diagnostics=model.diagnostics,
    )


def exposure_response(model: AnalyticsReadModel) -> CounterpartyExposureResponse:
    grouped_values: defaultdict[str, Decimal] = defaultdict(Decimal)
    labels: dict[str, str] = {}
    account_ids: defaultdict[str, set[UUID]] = defaultdict(set)
    account_types: defaultdict[str, set[AccountType]] = defaultdict(set)
    included = 0
    excluded = 0
    for holding in model.holdings:
        key = holding.institution_key
        labels[key] = holding.institution_label
        account_ids[key].add(holding.account_id)
        account_types[key].add(holding.response.account_type)
        if holding.response.market_value is None:
            excluded += 1
        else:
            included += 1
            grouped_values[key] += holding.response.market_value
    denominator = sum(grouped_values.values(), start=Decimal(0))
    quality = _quality(included, excluded)
    if not model.snapshot_fresh and quality is DataQualityState.COMPLETE:
        quality = DataQualityState.PARTIAL
    weights = _normalized_weights(grouped_values, denominator)
    return CounterpartyExposureResponse(
        portfolio_id=model.portfolio.id,
        valuation_as_of=model.valuation_as_of,
        reporting_currency=model.reporting_currency,
        snapshot_fresh=model.snapshot_fresh,
        quality=quality,
        denominator=denominator,
        included_components=included,
        excluded_components=excluded,
        items=[
            CounterpartyExposureItemResponse(
                key=key,
                institution_name=labels[key],
                account_types=sorted(account_types[key]),
                account_count=len(account_ids[key]),
                known_value=value,
                weight=weights.get(key),
            )
            for key, value in sorted(grouped_values.items())
        ],
        diagnostics=model.diagnostics,
    )


def allocation_response(model: AnalyticsReadModel) -> ActualAllocationResponse:
    actual = calculate_actual_allocation(
        [
            AllocationValuationComponent(
                category_id=item.category_id,
                value=item.response.market_value,
            )
            for item in model.holdings
        ]
    )
    items = [
        ActualAllocationItemResponse(
            category=AllocationCategoryResponse.model_validate(model.categories[item.category_id]),
            known_value=item.known_value,
            actual_weight=item.weight,
        )
        for item in actual.weights
    ]
    diagnostics = [*model.diagnostics]
    diagnostics.extend(
        _diagnostic("warning", code, _allocation_diagnostic_message(code))
        for code in actual.diagnostic_codes
    )
    quality = actual.quality
    if not model.snapshot_fresh and quality is DataQualityState.COMPLETE:
        quality = DataQualityState.PARTIAL
    return ActualAllocationResponse(
        portfolio_id=model.portfolio.id,
        valuation_as_of=model.valuation_as_of,
        reporting_currency=model.reporting_currency,
        snapshot_fresh=model.snapshot_fresh,
        quality=quality,
        denominator=actual.denominator,
        included_components=actual.included_components,
        excluded_components=actual.excluded_components,
        items=items,
        diagnostics=_deduplicate_diagnostics(diagnostics),
    )


def _build_metrics(
    *,
    positions: list[CalculatedPositionModel],
    cash_balances: list[CalculatedCashBalanceModel],
    currency_metrics: list[CalculatedCurrencyMetricsModel],
    reporting_currency: str,
    valuation_as_of: datetime,
    converter: CurrencyConverter,
    snapshot_fresh: bool,
) -> dict[CurrentMetric, CurrentMetricResponse]:
    components: list[MetricComponent] = []
    for position in positions:
        components.extend(
            (
                MetricComponent(
                    CurrentMetric.CURRENT_VALUE,
                    position.market_value,
                    position.valuation_currency,
                ),
                MetricComponent(
                    CurrentMetric.COST_BASIS,
                    position.cost_basis,
                    position.cost_currency,
                ),
                MetricComponent(
                    CurrentMetric.UNREALISED_PNL,
                    position.unrealised_pnl,
                    position.valuation_currency or position.cost_currency,
                ),
            )
        )
        if position.realised_pnl is None:
            components.append(
                MetricComponent(
                    CurrentMetric.REALISED_PNL,
                    None,
                    None,
                )
            )
    for cash in cash_balances:
        components.append(
            MetricComponent(
                CurrentMetric.CURRENT_VALUE,
                cash.amount,
                cash.currency,
            )
        )
    for item in currency_metrics:
        components.extend(
            (
                MetricComponent(
                    CurrentMetric.REALISED_PNL,
                    item.realised_pnl,
                    item.currency,
                ),
                MetricComponent(
                    CurrentMetric.INCOME,
                    item.income,
                    item.currency,
                ),
                MetricComponent(
                    CurrentMetric.FEES,
                    item.fees,
                    item.currency,
                ),
                MetricComponent(
                    CurrentMetric.TAXES,
                    item.taxes,
                    item.currency,
                ),
            )
        )
    metrics = (
        CurrentMetric.CURRENT_VALUE,
        CurrentMetric.COST_BASIS,
        CurrentMetric.REALISED_PNL,
        CurrentMetric.UNREALISED_PNL,
        CurrentMetric.INCOME,
        CurrentMetric.FEES,
        CurrentMetric.TAXES,
    )
    result: dict[CurrentMetric, CurrentMetricResponse] = {}
    for metric in metrics:
        metric_components = list(components)
        if not snapshot_fresh:
            metric_components.append(MetricComponent(metric, None, None))
        aggregation = aggregate_current_metric(
            metric,
            metric_components,
            reporting_currency=reporting_currency,
            converter=converter,
        )
        result[metric] = metric_response(
            aggregation,
            valuation_as_of=valuation_as_of,
        )
    return result


async def _resolve_rates(
    session: AsyncSession,
    *,
    source_currencies: set[str],
    reporting_currency: str,
    valuation_as_of: datetime,
    settings: Settings,
) -> dict[str, ResolvedFxRate]:
    return {
        currency: await resolve_exchange_rate(
            session,
            base_currency=currency,
            quote_currency=reporting_currency,
            valuation_as_of=valuation_as_of,
            stale_after_seconds=settings.fx_stale_after_seconds,
        )
        for currency in sorted(source_currencies)
    }


def _converter(rates: dict[str, ResolvedFxRate]) -> CurrencyConverter:
    def convert(amount: Decimal, source: str, target: str) -> Decimal | None:
        del target
        rate = rates.get(source)
        if rate is None or rate.rate is None:
            return None
        return amount * rate.rate

    return convert


def _convert_money(
    amount: Decimal | None,
    source_currency: str | None,
    reporting_currency: str,
    rates: dict[str, ResolvedFxRate],
) -> Decimal | None:
    if amount is None or source_currency is None:
        return None
    if source_currency == reporting_currency:
        return amount
    resolved = rates.get(source_currency)
    if resolved is None or resolved.rate is None:
        return None
    with localcontext(_ANALYTICS_CONTEXT):
        return validate_decimal(
            (amount * resolved.rate).quantize(
                _MONEY_QUANTUM,
                rounding=ROUND_HALF_EVEN,
            ),
            MONEY_SPEC,
        )


def _normalized_weights(
    values: dict[str, Decimal],
    denominator: Decimal,
) -> dict[str, Decimal]:
    if not values or denominator <= 0:
        return {}
    with localcontext(_ANALYTICS_CONTEXT):
        weights = {
            key: validate_decimal(
                (value / denominator).quantize(
                    _RATE_QUANTUM,
                    rounding=ROUND_HALF_EVEN,
                ),
                RATE_SPEC,
            )
            for key, value in values.items()
        }
        residual = Decimal(1) - sum(weights.values(), start=Decimal(0))
        if residual:
            residual_key = min(values, key=lambda key: (-values[key], key))
            weights[residual_key] = validate_decimal(
                weights[residual_key] + residual,
                RATE_SPEC,
            )
    return weights


def _holding_projection(
    response: AnalyticsHoldingResponse,
    account: AccountModel,
    instrument: InstrumentModel | None,
    category: AllocationCategoryModel,
    source_currency: str,
) -> HoldingProjection:
    institution_key = (
        f"institution:{account.institution_name.casefold()}"
        if account.institution_name
        else f"account:{account.id}"
    )
    institution_label = account.institution_name or "Unspecified institution"
    return HoldingProjection(
        response=response,
        category_id=category.id,
        account_id=account.id,
        institution_key=institution_key,
        institution_label=institution_label,
        breakdown_keys={
            BreakdownDimension.ACCOUNT: (str(account.id), account.name),
            BreakdownDimension.INSTITUTION: (institution_key, institution_label),
            BreakdownDimension.CURRENCY: (source_currency, source_currency),
            BreakdownDimension.INSTRUMENT_TYPE: (
                instrument.instrument_type.value if instrument is not None else "cash",
                instrument.instrument_type.value if instrument is not None else "cash",
            ),
            BreakdownDimension.ALLOCATION_CATEGORY: (str(category.id), category.name),
        },
    )


def _select_reporting_currency(
    portfolio: PortfolioModel,
    requested: ReportingCurrency | None,
    diagnostics: list[AnalyticsDiagnosticResponse],
) -> str:
    if requested is not None:
        return requested.value
    if portfolio.base_currency in {item.value for item in ReportingCurrency}:
        return portfolio.base_currency
    diagnostics.append(
        _diagnostic(
            "warning",
            "reporting_currency_defaulted",
            "The portfolio base currency is unsupported for current analytics; RUB was used",
        )
    )
    return ReportingCurrency.RUB.value


def _holding_quality(
    market_value: Decimal | None,
    diagnostics: list[AnalyticsDiagnosticResponse],
) -> DataQualityState:
    if market_value is None:
        return DataQualityState.UNAVAILABLE
    if diagnostics:
        return DataQualityState.PARTIAL
    return DataQualityState.COMPLETE


def _quality(included: int, excluded: int) -> DataQualityState:
    if excluded == 0:
        return DataQualityState.COMPLETE
    if included:
        return DataQualityState.PARTIAL
    return DataQualityState.UNAVAILABLE


def _rate_diagnostics(
    rates: dict[str, ResolvedFxRate],
) -> list[AnalyticsDiagnosticResponse]:
    result: list[AnalyticsDiagnosticResponse] = []
    if any(item.status == "stale" for item in rates.values()):
        result.append(
            _diagnostic(
                "warning",
                "fx_rate_stale",
                "At least one current metric uses an old exchange rate",
            )
        )
    if any(item.status == "unavailable" for item in rates.values()):
        result.append(
            _diagnostic(
                "warning",
                "fx_rate_missing",
                "At least one current metric cannot be converted",
            )
        )
    return result


def _currency_rate_diagnostics(
    rates: dict[str, ResolvedFxRate],
    currency: str,
) -> list[AnalyticsDiagnosticResponse]:
    rate = rates.get(currency)
    if rate is None or rate.status == "fresh":
        return []
    if rate.status == "stale":
        return [
            _diagnostic(
                "warning",
                "fx_rate_stale",
                "The holding uses an old exchange rate",
            )
        ]
    return [
        _diagnostic(
            "warning",
            "fx_rate_missing",
            "The holding cannot be converted to the reporting currency",
        )
    ]


def _calculation_diagnostics(
    values: list[dict[str, object]],
) -> list[AnalyticsDiagnosticResponse]:
    result: list[AnalyticsDiagnosticResponse] = []
    for item in values:
        code = item.get("code")
        message = item.get("message")
        severity = item.get("severity")
        if isinstance(code, str) and isinstance(message, str):
            result.append(
                _diagnostic(
                    "error" if severity == "error" else "warning",
                    code,
                    message,
                )
            )
    return result


def _diagnostic(
    severity: str,
    code: str,
    message: str,
) -> AnalyticsDiagnosticResponse:
    return AnalyticsDiagnosticResponse.model_validate(
        {"severity": severity, "code": code, "message": message}
    )


def _deduplicate_diagnostics(
    values: list[AnalyticsDiagnosticResponse],
) -> list[AnalyticsDiagnosticResponse]:
    unique: dict[tuple[str, str], AnalyticsDiagnosticResponse] = {}
    for item in values:
        unique[(item.severity, item.code)] = item
    return [unique[key] for key in sorted(unique)]


def _allocation_diagnostic_message(code: str) -> str:
    if code == "allocation_component_unavailable":
        return "Actual weights use only evaluated holdings; coverage is partial"
    if code == "allocation_negative_component":
        return (
            "Actual allocation contains a negative balance; signed weights are exact "
            "but are not suitable for a pie chart"
        )
    return "Actual weights are unavailable because the known denominator is not positive"
