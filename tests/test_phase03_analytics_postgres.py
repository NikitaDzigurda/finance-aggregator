import os
from collections.abc import AsyncIterator
from datetime import UTC, datetime
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
from pricing.models import ExchangeRateModel, MarketPriceModel
from shared.database import get_db_session


@pytest.mark.postgres
@pytest.mark.asyncio
async def test_current_analytics_are_consistent_partial_exact_and_freshness_aware() -> None:
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
    suffix = uuid4().hex[:8].upper()
    portfolio_ids: list[UUID] = []
    instrument_ids: list[UUID] = []
    try:
        async with AsyncClient(
            transport=ASGITransport(app=application), base_url="http://test"
        ) as client:
            portfolio = await client.post(
                "/api/v1/portfolios",
                json={"name": f"Synthetic analytics {suffix}", "base_currency": "USD"},
            )
            assert portfolio.status_code == 201, portfolio.text
            portfolio_id = UUID(portfolio.json()["id"])
            portfolio_ids.append(portfolio_id)
            empty = await client.post(
                "/api/v1/portfolios",
                json={"name": f"Synthetic empty {suffix}", "base_currency": "USD"},
            )
            assert empty.status_code == 201, empty.text
            empty_portfolio_id = UUID(empty.json()["id"])
            portfolio_ids.append(empty_portfolio_id)

            account_ids: list[str] = []
            for name, account_type, institution in (
                ("Synthetic broker account", "broker", "Synthetic Broker"),
                ("Synthetic CEX account", "cex", "Synthetic Exchange"),
            ):
                account = await client.post(
                    f"/api/v1/portfolios/{portfolio_id}/accounts",
                    json={
                        "name": name,
                        "account_type": account_type,
                        "institution_name": institution,
                    },
                )
                assert account.status_code == 201, account.text
                account_ids.append(account.json()["id"])

            for index, (name, instrument_type) in enumerate(
                (
                    ("Synthetic equity", "stock"),
                    ("Synthetic crypto", "crypto_asset"),
                    ("Synthetic missing price", "stock"),
                ),
                start=1,
            ):
                instrument = await client.post(
                    "/api/v1/instruments",
                    json={
                        "name": f"{name} {suffix}",
                        "instrument_type": instrument_type,
                        "currency": "USD",
                        "identifiers": [
                            {
                                "identifier_type": "provider_code",
                                "value": f"{suffix}{index}",
                                "provider": "phase03_analytics_synthetic",
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
                    "occurred_at": "2098-01-01T10:00:00Z",
                    "operation_type": "cash_movement",
                    "payload": {"direction": "deposit", "amount": "1000", "currency": "USD"},
                },
                {
                    **common,
                    "account_id": account_ids[0],
                    "occurred_at": "2098-01-02T10:00:00Z",
                    "operation_type": "trade",
                    "payload": {
                        "side": "buy",
                        "instrument_id": str(instrument_ids[0]),
                        "quantity": "2",
                        "price": "100",
                        "price_currency": "USD",
                    },
                },
                {
                    **common,
                    "account_id": account_ids[0],
                    "occurred_at": "2098-01-03T10:00:00Z",
                    "operation_type": "trade",
                    "payload": {
                        "side": "buy",
                        "instrument_id": str(instrument_ids[2]),
                        "quantity": "1",
                        "price": "50",
                        "price_currency": "USD",
                    },
                },
                {
                    **common,
                    "account_id": account_ids[1],
                    "occurred_at": "2098-01-01T11:00:00Z",
                    "operation_type": "cash_movement",
                    "payload": {"direction": "deposit", "amount": "500", "currency": "USD"},
                },
                {
                    **common,
                    "account_id": account_ids[1],
                    "occurred_at": "2098-01-02T11:00:00Z",
                    "operation_type": "trade",
                    "payload": {
                        "side": "buy",
                        "instrument_id": str(instrument_ids[1]),
                        "quantity": "1",
                        "price": "100",
                        "price_currency": "USD",
                    },
                },
            )
            for payload in operations:
                response = await client.post("/api/v1/operations", json=payload)
                assert response.status_code == 201, response.text

            prices = await client.post(
                "/api/v1/prices/batch",
                json={
                    "items": [
                        {
                            "instrument_id": str(instrument_ids[0]),
                            "price": "150",
                            "currency": "USD",
                            "observed_at": "2098-01-04T00:00:00Z",
                        },
                        {
                            "instrument_id": str(instrument_ids[1]),
                            "price": "200",
                            "currency": "USD",
                            "observed_at": "2098-01-04T00:00:00Z",
                        },
                    ]
                },
            )
            assert prices.status_code == 201, prices.text
            rate = await client.post(
                "/api/v1/fx-rates/batch",
                json={
                    "items": [
                        {
                            "base_currency": "USD",
                            "quote_currency": "RUB",
                            "rate": "80",
                            "observed_at": "2098-01-04T00:00:00Z",
                        }
                    ]
                },
            )
            assert rate.status_code == 201, rate.text

            recalculated = await client.post(
                f"/api/v1/portfolios/{portfolio_id}/positions/recalculate",
                json={"as_of": "2098-01-05T00:00:00Z"},
            )
            assert recalculated.status_code == 200, recalculated.text

            overview = await client.get(
                f"/api/v1/portfolios/{portfolio_id}/analytics/overview",
                params={"reporting_currency": "USD"},
            )
            assert overview.status_code == 200, overview.text
            overview_body = overview.json()
            assert overview_body["reporting_currency"] == "USD"
            assert overview_body["snapshot_fresh"] is True
            assert overview_body["current_value"]["quality"] == "partial"
            assert overview_body["current_value"]["total_value"] is None
            assert overview_body["current_value"]["known_value"] == ("1650.000000000000000000")
            assert overview_body["cost_basis"]["total_value"] == ("350.000000000000000000")

            holdings = await client.get(
                f"/api/v1/portfolios/{portfolio_id}/analytics/holdings",
                params={"reporting_currency": "USD"},
            )
            assert holdings.status_code == 200, holdings.text
            holding_values = [
                Decimal(item["market_value"])
                for item in holdings.json()["items"]
                if item["market_value"] is not None
            ]
            assert sum(holding_values, start=Decimal(0)) == Decimal("1650")
            assert {item["account_type"] for item in holdings.json()["items"]} == {
                "broker",
                "cex",
            }

            data_quality = await client.get(
                f"/api/v1/portfolios/{portfolio_id}/analytics/data-quality",
                params={"reporting_currency": "RUB"},
            )
            assert data_quality.status_code == 200, data_quality.text
            data_quality_body = data_quality.json()
            assert "market_price_missing" in {
                item["code"] for item in data_quality_body["diagnostics"]
            }
            provenance = data_quality_body["provenance"]
            assert provenance["calculation_contract_version"] == 2
            assert provenance["snapshot_fresh"] is True
            assert provenance["used_market_price_observation_count"] == 2
            assert provenance["used_market_price_observed_at"] == [
                "2098-01-04T00:00:00Z",
                "2098-01-04T00:00:00Z",
            ]
            assert provenance["used_fx_observation_count"] == 1
            assert provenance["used_fx_observed_at"] == ["2098-01-04T00:00:00Z"]
            assert "instrument_id" not in data_quality.text
            assert "account_id" not in data_quality.text

            breakdown = await client.get(
                f"/api/v1/portfolios/{portfolio_id}/analytics/breakdown",
                params={"reporting_currency": "USD"},
            )
            assert breakdown.status_code == 200, breakdown.text
            for dimension in breakdown.json()["dimensions"]:
                assert sum(
                    (Decimal(item["known_value"]) for item in dimension["items"]),
                    start=Decimal(0),
                ) == Decimal("1650")
                assert dimension["denominator"] == "1650.000000000000000000"

            exposure = await client.get(
                f"/api/v1/portfolios/{portfolio_id}/analytics/exposure",
                params={"reporting_currency": "USD"},
            )
            assert exposure.status_code == 200, exposure.text
            assert {item["institution_name"] for item in exposure.json()["items"]} == {
                "Synthetic Broker",
                "Synthetic Exchange",
            }

            allocation = await client.get(
                f"/api/v1/portfolios/{portfolio_id}/allocation",
                params={"reporting_currency": "USD"},
            )
            assert allocation.status_code == 200, allocation.text
            allocation_body = allocation.json()
            assert allocation_body["quality"] == "partial"
            assert allocation_body["denominator"] == "1650.000000000000000000"
            assert sum(
                (
                    Decimal(item["actual_weight"])
                    for item in allocation_body["items"]
                    if item["actual_weight"] is not None
                ),
                start=Decimal(0),
            ) == Decimal(1)
            assert all("trade" not in item for item in allocation_body["items"])

            rub_overview = await client.get(
                f"/api/v1/portfolios/{portfolio_id}/analytics/overview",
                params={"reporting_currency": "RUB"},
            )
            assert rub_overview.status_code == 200, rub_overview.text
            assert rub_overview.json()["reporting_currency"] == "RUB"
            assert rub_overview.json()["current_value"]["known_value"] == (
                "132000.000000000000000000"
            )

            stale_operation = await client.post(
                "/api/v1/operations",
                json={
                    **common,
                    "account_id": account_ids[0],
                    "occurred_at": "2098-01-05T01:00:00Z",
                    "operation_type": "fee",
                    "payload": {"amount": "1", "currency": "USD"},
                },
            )
            assert stale_operation.status_code == 201, stale_operation.text
            stale = await client.get(
                f"/api/v1/portfolios/{portfolio_id}/analytics/overview",
                params={"reporting_currency": "USD"},
            )
            assert stale.status_code == 200, stale.text
            assert stale.json()["snapshot_fresh"] is False
            assert stale.json()["current_value"]["total_value"] is None
            assert "calculation_snapshot_stale" in {
                item["code"] for item in stale.json()["diagnostics"]
            }

            for path in (
                "analytics/overview",
                "analytics/holdings",
                "analytics/breakdown",
                "analytics/exposure",
                "allocation",
            ):
                response = await client.get(
                    f"/api/v1/portfolios/{empty_portfolio_id}/{path}",
                    params={"reporting_currency": "USD"},
                )
                assert response.status_code == 200, response.text
            empty_overview = await client.get(
                f"/api/v1/portfolios/{empty_portfolio_id}/analytics/overview"
            )
            assert Decimal(empty_overview.json()["current_value"]["total_value"]) == Decimal(0)
    finally:
        async with sessions.begin() as session:
            from calculation.models import CalculationSnapshotModel

            if portfolio_ids:
                await session.execute(
                    delete(CalculationSnapshotModel).where(
                        CalculationSnapshotModel.portfolio_id.in_(portfolio_ids)
                    )
                )
            if instrument_ids:
                await session.execute(
                    delete(MarketPriceModel).where(
                        MarketPriceModel.instrument_id.in_(instrument_ids)
                    )
                )
            if portfolio_ids:
                await session.execute(
                    delete(OperationModel).where(OperationModel.portfolio_id.in_(portfolio_ids))
                )
                await session.execute(
                    delete(PortfolioModel).where(PortfolioModel.id.in_(portfolio_ids))
                )
            if instrument_ids:
                await session.execute(
                    delete(InstrumentModel).where(InstrumentModel.id.in_(instrument_ids))
                )
            await session.execute(
                delete(ExchangeRateModel).where(
                    ExchangeRateModel.provider == "manual",
                    ExchangeRateModel.observed_at == datetime(2098, 1, 4, tzinfo=UTC),
                )
            )
        application.dependency_overrides.clear()
        await engine.dispose()
