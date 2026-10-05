import os
from collections.abc import AsyncIterator
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from uuid import UUID, uuid4

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import delete
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from apps.api.main import create_app
from instruments.models import InstrumentModel
from operations.models import OperationModel
from portfolios.models import PortfolioModel
from pricing.models import ExchangeRateModel
from shared.database import get_db_session


@pytest.mark.postgres
@pytest.mark.asyncio
async def test_event_timelines_use_local_boundaries_and_original_units() -> None:
    database_url = os.getenv("FINANCE_TEST_DATABASE_URL")
    if database_url is None:
        pytest.skip("FINANCE_TEST_DATABASE_URL is not configured")

    engine = create_async_engine(database_url)
    sessions = async_sessionmaker(engine, expire_on_commit=False)

    async def override_session() -> AsyncIterator[AsyncSession]:
        async with sessions() as session:
            try:
                yield session
            except Exception:
                await session.rollback()
                raise

    application = create_app()
    application.dependency_overrides[get_db_session] = override_session
    portfolio_id: UUID | None = None
    instrument_ids: list[UUID] = []
    fx_observed_at = datetime(2096, 2, 1, tzinfo=UTC)
    try:
        async with AsyncClient(
            transport=ASGITransport(app=application),
            base_url="http://test",
        ) as client:
            portfolio = await client.post(
                "/api/v1/portfolios",
                json={
                    "name": f"Synthetic timelines {uuid4().hex[:8]}",
                    "base_currency": "USD",
                },
            )
            assert portfolio.status_code == 201, portfolio.text
            portfolio_id = UUID(portfolio.json()["id"])

            account_ids: list[str] = []
            for name, account_type in (
                ("Synthetic broker timeline", "broker"),
                ("Synthetic CEX timeline", "cex"),
            ):
                account = await client.post(
                    f"/api/v1/portfolios/{portfolio_id}/accounts",
                    json={"name": name, "account_type": account_type},
                )
                assert account.status_code == 201, account.text
                account_ids.append(account.json()["id"])

            for index, (name, instrument_type) in enumerate(
                (
                    ("Synthetic timeline equity", "stock"),
                    ("Synthetic timeline crypto", "crypto_asset"),
                ),
                start=1,
            ):
                identifier = uuid4().hex.upper()
                instrument = await client.post(
                    "/api/v1/instruments",
                    json={
                        "name": f"{name} {uuid4().hex[:8]}",
                        "instrument_type": instrument_type,
                        "currency": "USD",
                        "identifiers": [
                            {
                                "identifier_type": "provider_code",
                                "value": f"{identifier}{index}",
                                "provider": "phase03_timeline_synthetic",
                            }
                        ],
                    },
                )
                assert instrument.status_code == 201, instrument.text
                instrument_ids.append(UUID(instrument.json()["id"]))

            common = {
                "portfolio_id": str(portfolio_id),
                "time_precision": "second",
            }
            operations = (
                {
                    **common,
                    "account_id": account_ids[0],
                    "occurred_at": "2096-01-01T21:30:00Z",
                    "operation_type": "cash_movement",
                    "payload": {
                        "direction": "deposit",
                        "amount": "1000",
                        "currency": "USD",
                    },
                },
                {
                    **common,
                    "account_id": account_ids[0],
                    "occurred_at": "2096-01-02T22:00:00Z",
                    "operation_type": "cash_movement",
                    "payload": {
                        "direction": "withdrawal",
                        "amount": "10",
                        "currency": "USD",
                    },
                },
                {
                    **common,
                    "account_id": account_ids[0],
                    "occurred_at": "2096-01-04T10:00:00Z",
                    "operation_type": "income",
                    "payload": {
                        "income_type": "dividend",
                        "amount": "5",
                        "currency": "USD",
                    },
                },
                {
                    **common,
                    "account_id": account_ids[0],
                    "occurred_at": "2096-01-05T10:00:00Z",
                    "operation_type": "fee",
                    "payload": {"amount": "3", "currency": "USD"},
                },
                {
                    **common,
                    "account_id": account_ids[0],
                    "occurred_at": "2096-01-05T11:00:00Z",
                    "operation_type": "tax",
                    "payload": {"amount": "2", "currency": "USD"},
                },
                {
                    **common,
                    "account_id": account_ids[0],
                    "occurred_at": "2096-01-06T10:00:00Z",
                    "operation_type": "trade",
                    "payload": {
                        "side": "buy",
                        "instrument_id": str(instrument_ids[0]),
                        "quantity": "10",
                        "price": "100",
                        "price_currency": "USD",
                    },
                },
                {
                    **common,
                    "account_id": account_ids[0],
                    "occurred_at": "2096-01-07T10:00:00Z",
                    "operation_type": "trade",
                    "payload": {
                        "side": "sell",
                        "instrument_id": str(instrument_ids[0]),
                        "quantity": "4",
                        "price": "150",
                        "price_currency": "USD",
                    },
                },
                {
                    **common,
                    "account_id": account_ids[1],
                    "occurred_at": "2096-01-07T11:00:00Z",
                    "operation_type": "crypto_transfer",
                    "payload": {
                        "direction": "inbound",
                        "instrument_id": str(instrument_ids[1]),
                        "quantity": "2",
                    },
                },
            )
            for payload in operations:
                response = await client.post("/api/v1/operations", json=payload)
                assert response.status_code == 201, response.text

            recalculated = await client.post(
                f"/api/v1/portfolios/{portfolio_id}/positions/recalculate",
                json={"as_of": "2096-02-01T00:00:00Z"},
            )
            assert recalculated.status_code == 200, recalculated.text

            base_params = {
                "from": "2096-01-02",
                "to": "2096-02-02",
                "timezone": "Europe/Moscow",
            }
            cash_flows = await client.get(
                f"/api/v1/portfolios/{portfolio_id}/analytics/cash-flows",
                params={**base_params, "bucket": "day"},
            )
            assert cash_flows.status_code == 200, cash_flows.text
            assert cash_flows.json()["series_total"] == 2
            assert cash_flows.json()["limit"] == 100
            assert cash_flows.json()["offset"] == 0
            assert {item["key"] for item in cash_flows.json()["series"]} == {
                "deposits:currency:USD",
                "withdrawals:currency:USD",
            }
            cash_series = _series(cash_flows.json())
            assert cash_series[("deposits", "currency", "USD")] == [
                ("2096-01-02", Decimal("1000"))
            ]
            assert cash_series[("withdrawals", "currency", "USD")] == [
                ("2096-01-03", Decimal("10"))
            ]

            income = await client.get(
                f"/api/v1/portfolios/{portfolio_id}/analytics/income",
                params={**base_params, "bucket": "week"},
            )
            assert income.status_code == 200, income.text
            expected_week = date(2096, 1, 4) - timedelta(days=date(2096, 1, 4).weekday())
            assert _series(income.json())[("income", "currency", "USD")] == [
                (expected_week.isoformat(), Decimal("5"))
            ]

            costs = await client.get(
                f"/api/v1/portfolios/{portfolio_id}/analytics/costs",
                params={**base_params, "bucket": "month"},
            )
            assert costs.status_code == 200, costs.text
            cost_series = _series(costs.json())
            assert cost_series[("fees", "currency", "USD")] == [
                ("2096-01-01", Decimal("3"))
            ]
            assert cost_series[("taxes", "currency", "USD")] == [
                ("2096-01-01", Decimal("2"))
            ]
            assert "total_result" not in costs.text

            trading = await client.get(
                f"/api/v1/portfolios/{portfolio_id}/analytics/trading",
                params={**base_params, "bucket": "day"},
            )
            assert trading.status_code == 200, trading.text
            trading_body = trading.json()
            trading_series = _series(trading_body)
            assert trading_series[("trade_turnover", "currency", "USD")] == [
                ("2096-01-06", Decimal("1000")),
                ("2096-01-07", Decimal("600")),
            ]
            assert trading_series[("realised_pnl", "currency", "USD")] == [
                ("2096-01-07", Decimal("200"))
            ]
            assert all(item[1] == "currency" for item in trading_series)
            assert trading_body["provenance"]["calculation_contract_version"] == 2
            assert trading_body["provenance"]["operation_effects_contract_version"] == 1
            assert trading_body["provenance"]["snapshot_fresh"] is True
            assert trading_body["historical_reporting_conversion"] == "unavailable"

            rate = await client.post(
                "/api/v1/fx-rates/batch",
                json={
                    "items": [
                        {
                            "base_currency": "USD",
                            "quote_currency": "RUB",
                            "rate": "100",
                            "observed_at": "2096-02-01T00:00:00Z",
                        }
                    ]
                },
            )
            assert rate.status_code == 201, rate.text
            after_rate = await client.get(
                f"/api/v1/portfolios/{portfolio_id}/analytics/trading",
                params={**base_params, "bucket": "day"},
            )
            assert after_rate.status_code == 200, after_rate.text
            assert after_rate.json()["series"] == trading_body["series"]

            paged_costs = await client.get(
                f"/api/v1/portfolios/{portfolio_id}/analytics/costs",
                params={
                    **base_params,
                    "bucket": "month",
                    "unit_type": "currency",
                    "unit": "USD",
                    "limit": 1,
                    "offset": 1,
                },
            )
            assert paged_costs.status_code == 200, paged_costs.text
            assert paged_costs.json()["series_total"] == 2
            assert paged_costs.json()["limit"] == 1
            assert paged_costs.json()["offset"] == 1
            assert [item["key"] for item in paged_costs.json()["series"]] == [
                "taxes:currency:USD"
            ]

            empty_page = await client.get(
                f"/api/v1/portfolios/{portfolio_id}/analytics/income",
                params={**base_params, "bucket": "day", "unit": "EUR"},
            )
            assert empty_page.status_code == 200, empty_page.text
            assert empty_page.json()["series_total"] == 0
            assert empty_page.json()["series"] == []

            oversized = await client.get(
                f"/api/v1/portfolios/{portfolio_id}/analytics/cash-flows",
                params={
                    "from": "2096-01-01",
                    "to": "2097-01-02",
                    "bucket": "day",
                    "timezone": "Europe/Moscow",
                },
            )
            assert oversized.status_code == 422
            assert oversized.json()["error"]["code"] == "analytics_period_too_large"

            bad_timezone = await client.get(
                f"/api/v1/portfolios/{portfolio_id}/analytics/cash-flows",
                params={**base_params, "bucket": "day", "timezone": "Not/AZone"},
            )
            assert bad_timezone.status_code == 422
            assert bad_timezone.json()["error"]["code"] == "analytics_timezone_invalid"
    finally:
        async with sessions.begin() as session:
            await session.execute(
                delete(ExchangeRateModel).where(
                    ExchangeRateModel.provider == "manual",
                    ExchangeRateModel.observed_at == fx_observed_at,
                )
            )
            if portfolio_id is not None:
                await session.execute(
                    delete(OperationModel).where(
                        OperationModel.portfolio_id == portfolio_id
                    )
                )
                await session.execute(
                    delete(PortfolioModel).where(PortfolioModel.id == portfolio_id)
                )
            if instrument_ids:
                await session.execute(
                    delete(InstrumentModel).where(InstrumentModel.id.in_(instrument_ids))
                )
        application.dependency_overrides.clear()
        await engine.dispose()


def _series(body: dict[str, object]) -> dict[tuple[str, str, str], list[tuple[str, Decimal]]]:
    result: dict[tuple[str, str, str], list[tuple[str, Decimal]]] = {}
    raw_series = body["series"]
    assert isinstance(raw_series, list)
    for item in raw_series:
        assert isinstance(item, dict)
        buckets = item["buckets"]
        assert isinstance(buckets, list)
        result[(str(item["metric"]), str(item["unit_type"]), str(item["unit"]))] = [
            (str(bucket["bucket_start"]), Decimal(str(bucket["value"])))
            for bucket in buckets
            if isinstance(bucket, dict)
        ]
    return result
