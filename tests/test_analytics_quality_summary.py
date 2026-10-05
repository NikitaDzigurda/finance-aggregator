from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest

from analytics import quality_service
from analytics.contracts import CurrentMetric, DataQualityState
from shared.config import Settings


@pytest.mark.asyncio
async def test_quality_counts_positions_and_only_dependent_metrics(monkeypatch):
    """Repeated crypto executions must not look like extra unknown holdings."""
    account, other_account, asset, other_asset = (str(uuid4()) for _ in range(4))
    diagnostics = [
        dict(
            code="cost_basis_unknown",
            account_id=a,
            instrument_id=i,
            operation_id=str(uuid4()),
            severity="warning",
        )
        for a, i in [(account, asset)] * 8 + [(account, other_asset)] * 4 + [(other_account, asset)]
    ]
    now = datetime.now(UTC)
    model = SimpleNamespace(
        portfolio=SimpleNamespace(id=uuid4()),
        snapshot=SimpleNamespace(as_of=now, diagnostics=diagnostics),
        snapshot_fresh=True,
        valuation_as_of=now,
        reporting_currency="RUB",
        diagnostics=[],
        holdings=[
            SimpleNamespace(
                response=SimpleNamespace(diagnostics=[SimpleNamespace(code="market_price_missing")])
            )
        ],
        metrics={m: SimpleNamespace(quality=DataQualityState.PARTIAL) for m in CurrentMetric},
        market_price_observation_times=(),
        fx_observation_times=(),
    )
    monkeypatch.setattr(
        quality_service, "build_analytics_read_model", AsyncMock(return_value=model)
    )
    monkeypatch.setattr(
        quality_service,
        "get_portfolio_import_quality_summary",
        AsyncMock(
            return_value=SimpleNamespace(
                period_limited_batch_count=0,
                unknown_completeness_batch_count=0,
                reconciliation_mismatch_batch_count=0,
                unresolved_review_row_count=0,
            )
        ),
    )
    response = await quality_service.build_data_quality_response(
        None, portfolio_id=model.portfolio.id, reporting_currency="RUB", settings=Settings()
    )
    summaries = {item.code: item for item in response.diagnostics}
    assert summaries["cost_basis_unknown"].count == 3
    basis_impacts = {item.metric for item in summaries["cost_basis_unknown"].impacts}
    assert "unrealised_pnl" in basis_impacts
    assert "income" not in basis_impacts
    price_impacts = {item.metric for item in summaries["market_price_missing"].impacts}
    assert "cost_basis" not in price_impacts
    assert "realised_pnl" not in price_impacts
    assert "unrealised_pnl" in price_impacts
