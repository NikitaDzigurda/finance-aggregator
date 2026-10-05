from __future__ import annotations

from collections import Counter
from typing import Literal
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from analytics.contracts import CurrentMetric, DataQualityState, ReportingCurrency
from analytics.schemas import (
    AnalyticsDataQualityResponse,
    DataQualityDiagnosticSummaryResponse,
    DataQualityImpactResponse,
    DataQualityProvenanceResponse,
)
from analytics.service import AnalyticsReadModel, build_analytics_read_model
from calculation.contracts import CALCULATION_CONTRACT_VERSION
from imports.service import get_portfolio_import_quality_summary
from shared.config import Settings

_CALCULATION_CODES = {
    "cost_basis_unknown",
    "negative_position",
    "cost_currency_mismatch",
    "valuation_currency_mismatch",
    "corporate_action_basis_assumption",
}
_HOLDING_CODES = {
    "market_price_missing",
    "market_price_stale",
    "fx_rate_missing",
    "fx_rate_stale",
}
_SAFE_MESSAGES = {
    "import_history_period_limited": (
        "At least one active import contains only a bounded reporting period"
    ),
    "import_history_completeness_unknown": (
        "At least one active import does not establish Ledger history completeness"
    ),
    "import_reconciliation_mismatch": (
        "At least one active import does not match its report controls"
    ),
    "import_review_unresolved": "At least one import row still requires review",
    "calculation_snapshot_missing": "The current Ledger has no calculation snapshot",
    "calculation_snapshot_stale": "The calculation snapshot does not include current Ledger",
    "market_price_missing": "At least one holding has no usable market price",
    "market_price_stale": "At least one holding uses an old market price",
    "fx_rate_missing": "At least one current component cannot be converted",
    "fx_rate_stale": "At least one current component uses an old exchange rate",
    "cost_basis_unknown": "At least one holding has unknown acquisition cost",
    "negative_position": "At least one calculated position is negative",
    "cost_currency_mismatch": "At least one position uses incompatible cost currencies",
    "valuation_currency_mismatch": (
        "At least one valuation currency differs from its cost currency"
    ),
    "corporate_action_basis_assumption": (
        "At least one corporate action preserves basis under an explicit assumption"
    ),
}


async def build_data_quality_response(
    session: AsyncSession,
    *,
    portfolio_id: UUID,
    reporting_currency: ReportingCurrency | None,
    settings: Settings,
) -> AnalyticsDataQualityResponse:
    model = await build_analytics_read_model(
        session,
        portfolio_id=portfolio_id,
        reporting_currency=reporting_currency,
        settings=settings,
    )
    imports = await get_portfolio_import_quality_summary(
        session,
        portfolio_id=portfolio_id,
    )
    counts: Counter[tuple[str, Literal["warning", "error"]]] = Counter()
    _add(counts, "import_history_period_limited", imports.period_limited_batch_count)
    _add(
        counts,
        "import_history_completeness_unknown",
        imports.unknown_completeness_batch_count,
    )
    _add(
        counts,
        "import_reconciliation_mismatch",
        imports.reconciliation_mismatch_batch_count,
    )
    _add(counts, "import_review_unresolved", imports.unresolved_review_row_count)

    global_codes = {item.code for item in model.diagnostics}
    for code in ("calculation_snapshot_missing", "calculation_snapshot_stale"):
        if code in global_codes:
            _add(counts, code, 1, severity="error" if code.endswith("missing") else "warning")

    if model.snapshot is not None:
        seen_positions: set[tuple[str, str, str, str]] = set()
        for item in model.snapshot.diagnostics:
            raw_code = item.get("code")
            if not isinstance(raw_code, str) or raw_code not in _CALCULATION_CODES:
                continue
            severity: Literal["warning", "error"] = (
                "error" if item.get("severity") == "error" else "warning"
            )
            # Snapshot diagnostics include one entry per triggering operation.
            # The summary describes affected positions, not repeated events.
            scope = (
                raw_code,
                severity,
                str(item.get("account_id")),
                str(item.get("instrument_id") or item.get("operation_id")),
            )
            if scope in seen_positions:
                continue
            seen_positions.add(scope)
            _add(counts, raw_code, 1, severity=severity)

    holding_counts: Counter[str] = Counter()
    for holding in model.holdings:
        holding_counts.update(
            {
                item.code
                for item in holding.response.diagnostics
                if item.code in _HOLDING_CODES
            }
        )
    for code in _HOLDING_CODES:
        count = holding_counts[code]
        if count == 0 and code in global_codes:
            count = 1
        _add(counts, code, count)

    summaries = [
        DataQualityDiagnosticSummaryResponse(
            severity=severity,
            code=code,
            message=_SAFE_MESSAGES[code],
            count=count,
            impacts=_impacts(code, model),
        )
        for (code, severity), count in sorted(counts.items())
        if count > 0
    ]
    return AnalyticsDataQualityResponse(
        portfolio_id=model.portfolio.id,
        valuation_as_of=model.valuation_as_of,
        reporting_currency=model.reporting_currency,
        diagnostics=summaries,
        provenance=DataQualityProvenanceResponse(
            calculation_contract_version=CALCULATION_CONTRACT_VERSION,
            snapshot_as_of=model.snapshot.as_of if model.snapshot is not None else None,
            snapshot_fresh=model.snapshot_fresh,
            used_market_price_observation_count=len(
                model.market_price_observation_times
            ),
            used_market_price_observed_at=list(model.market_price_observation_times),
            used_fx_observation_count=len(model.fx_observation_times),
            used_fx_observed_at=list(model.fx_observation_times),
        ),
    )


def _add(
    counts: Counter[tuple[str, Literal["warning", "error"]]],
    code: str,
    count: int,
    *,
    severity: Literal["warning", "error"] = "warning",
) -> None:
    if count > 0:
        counts[(code, severity)] += count


def _impacts(
    code: str,
    model: AnalyticsReadModel,
) -> list[DataQualityImpactResponse]:
    if code.startswith("import_history_") or code == "import_reconciliation_mismatch":
        return [
            _impact("analytics/data-quality", "ledger_history", DataQualityState.PARTIAL),
            _metric_impact(model, CurrentMetric.CURRENT_VALUE),
            _impact("analytics/timelines", "ledger_events", DataQualityState.PARTIAL),
        ]
    if code == "import_review_unresolved":
        return [
            _impact("imports/preview", "review", DataQualityState.PARTIAL),
            _metric_impact(model, CurrentMetric.CURRENT_VALUE),
        ]
    if code in {"calculation_snapshot_missing", "calculation_snapshot_stale"}:
        return [
            _metric_impact(model, CurrentMetric.CURRENT_VALUE),
            _metric_impact(model, CurrentMetric.COST_BASIS),
            _metric_impact(model, CurrentMetric.REALISED_PNL),
            _impact(
                "analytics/holdings",
                "market_value",
                _metric_state(model, CurrentMetric.CURRENT_VALUE),
            ),
        ]
    if code == "market_price_missing":
        return [
            _impact(
                "analytics/holdings",
                "market_value",
                DataQualityState.UNAVAILABLE,
            ),
            _metric_impact(model, CurrentMetric.CURRENT_VALUE),
            _metric_impact(model, CurrentMetric.UNREALISED_PNL),
            _impact(
                "allocation",
                "actual_weight",
                _metric_state(model, CurrentMetric.CURRENT_VALUE),
            ),
        ]
    if code == "market_price_stale":
        return [
            _impact(
                "analytics/holdings",
                "market_value",
                DataQualityState.PARTIAL,
            ),
            _metric_impact(model, CurrentMetric.CURRENT_VALUE),
        ]
    if code in {"fx_rate_missing", "fx_rate_stale"}:
        return [
            _metric_impact(model, metric)
            for metric in (
                CurrentMetric.CURRENT_VALUE,
                CurrentMetric.COST_BASIS,
                CurrentMetric.REALISED_PNL,
                CurrentMetric.UNREALISED_PNL,
                CurrentMetric.INCOME,
                CurrentMetric.FEES,
                CurrentMetric.TAXES,
            )
        ]
    if code in _CALCULATION_CODES:
        return [
            _impact(
                "analytics/holdings",
                "cost_basis",
                DataQualityState.UNAVAILABLE,
            ),
            _metric_impact(model, CurrentMetric.COST_BASIS),
            _metric_impact(model, CurrentMetric.REALISED_PNL),
            _metric_impact(model, CurrentMetric.UNREALISED_PNL),
        ]
    return []


def _metric_impact(
    model: AnalyticsReadModel,
    metric: CurrentMetric,
) -> DataQualityImpactResponse:
    return _impact("analytics/overview", metric.value, _metric_state(model, metric))


def _metric_state(
    model: AnalyticsReadModel,
    metric: CurrentMetric,
) -> DataQualityState:
    return model.metrics[metric].quality


def _impact(
    endpoint: str,
    metric: str,
    state: DataQualityState,
) -> DataQualityImpactResponse:
    return DataQualityImpactResponse(endpoint=endpoint, metric=metric, state=state)
