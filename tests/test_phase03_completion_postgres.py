import os
from collections.abc import AsyncIterator
from decimal import Decimal
from pathlib import Path
from statistics import median
from time import perf_counter
from uuid import UUID, uuid4

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import delete, select, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from apps.api.main import create_app
from calculation.models import CalculationSnapshotModel
from imports.adapters import get_adapter_registry
from imports.models import ImportBatchFileModel, ImportBatchModel, ImportRowModel
from imports.processor import process_import_batch
from imports.storage import LocalObjectStorage, get_object_storage
from instruments.models import InstrumentModel
from operations.models import OperationModel
from portfolios.models import PortfolioModel
from pricing.models import ExchangeRateModel, MarketPriceModel
from shared.database import get_db_session

_FIXTURES = Path(__file__).parents[1] / "docs" / "fixtures"
_BYBIT_FILES = (
    "bybit_spot_trade_history_synthetic_v1.csv",
    "bybit_uta_asset_change_details_synthetic_v1.csv",
    "bybit_funding_asset_change_details_synthetic_v1.csv",
    "bybit_withdraw_deposit_history_synthetic_v1.csv",
)


async def _upload_and_process(
    client: AsyncClient,
    sessions: async_sessionmaker[AsyncSession],
    storage: LocalObjectStorage,
    *,
    portfolio_id: UUID,
    account_id: str,
    provider: str,
    declared_format: str,
    files: list[tuple[str, tuple[str, bytes, str]]],
) -> UUID:
    response = await client.post(
        "/api/v1/imports",
        data={
            "portfolio_id": str(portfolio_id),
            "account_id": account_id,
            "source_provider": provider,
            "declared_format": declared_format,
        },
        files=files,
    )
    assert response.status_code == 201, response.text
    batch_id = UUID(response.json()["batch"]["id"])
    await process_import_batch(
        sessions,
        storage,
        get_adapter_registry(),
        batch_id=batch_id,
    )
    return batch_id


async def _exclude_warnings_and_confirm(
    client: AsyncClient,
    batch_id: UUID,
) -> int:
    preview = await client.get(f"/api/v1/imports/{batch_id}/preview")
    assert preview.status_code == 200, preview.text
    for row in preview.json()["items"]:
        if row["status"] == "warning":
            excluded = await client.patch(
                f"/api/v1/imports/{batch_id}/rows/{row['id']}",
                json={
                    "action": "exclude",
                    "note": "Synthetic completion scenario review",
                },
            )
            assert excluded.status_code == 200, excluded.text
    confirmed = await client.post(f"/api/v1/imports/{batch_id}/confirm")
    assert confirmed.status_code == 200, confirmed.text
    return int(confirmed.json()["operation_count"])


@pytest.mark.postgres
@pytest.mark.asyncio
async def test_phase03_mixed_provider_swagger_scenario_and_benchmark(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    database_url = os.getenv("FINANCE_TEST_DATABASE_URL")
    if database_url is None:
        pytest.skip("FINANCE_TEST_DATABASE_URL is not configured")

    engine = create_async_engine(database_url)
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    storage = LocalObjectStorage(
        tmp_path / "phase03-completion-imports",
        max_file_size_bytes=512 * 1024,
    )

    async def override_session() -> AsyncIterator[AsyncSession]:
        async with sessions() as session:
            try:
                yield session
            except Exception:
                await session.rollback()
                raise

    application = create_app()
    application.dependency_overrides[get_db_session] = override_session
    application.dependency_overrides[get_object_storage] = lambda: storage
    portfolio_id: UUID | None = None
    instrument_ids: list[UUID] = []
    market_price_ids: list[UUID] = []
    exchange_rate_ids: list[UUID] = []
    storage_keys: list[str] = []
    try:
        async with AsyncClient(
            transport=ASGITransport(app=application),
            base_url="http://test",
        ) as client:
            openapi = await client.get("/openapi.json")
            assert openapi.status_code == 200
            assert set(
                openapi.json()["paths"][
                    "/api/v1/portfolios/{portfolio_id}/analytics/overview"
                ]["get"]["responses"]["200"]["content"]["application/json"][
                    "examples"
                ]
            ) == {"complete", "partial", "empty"}

            portfolio = await client.post(
                "/api/v1/portfolios",
                json={
                    "name": f"Synthetic mixed phase03 {uuid4().hex[:8]}",
                    "base_currency": "RUB",
                },
            )
            assert portfolio.status_code == 201, portfolio.text
            portfolio_id = UUID(portfolio.json()["id"])

            account_ids: dict[str, str] = {}
            for provider, name, account_type, institution in (
                ("tbank", "Synthetic T broker", "broker", "Synthetic T Institution"),
                ("alfa", "Synthetic Alfa broker", "broker", "Synthetic Alfa Institution"),
                ("bybit", "Synthetic Bybit Spot", "cex", "Synthetic CEX Institution"),
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
                account_ids[provider] = account.json()["id"]

            fund = await client.post(
                "/api/v1/instruments",
                json={
                    "name": f"Synthetic mixed fund {uuid4().hex[:8]}",
                    "instrument_type": "fund",
                    "currency": "RUB",
                    "identifiers": [
                        {
                            "identifier_type": "provider_code",
                            "value": "SYNBOND",
                            "provider": "tbank_broker_xlsx",
                        },
                        {
                            "identifier_type": "provider_code",
                            "value": "SYNBOND",
                            "provider": "alfa_broker_xml_import",
                        },
                    ],
                },
            )
            assert fund.status_code == 201, fund.text
            fund_id = UUID(fund.json()["id"])
            instrument_ids.append(fund_id)

            crypto_ids: dict[str, UUID] = {}
            for code in ("SYNTH", "USDT"):
                instrument = await client.post(
                    "/api/v1/instruments",
                    json={
                        "name": f"Synthetic mixed {code} {uuid4().hex[:8]}",
                        "instrument_type": "crypto_asset",
                        "currency": "USD",
                        "identifiers": [
                            {"identifier_type": "crypto_asset_code", "value": code}
                        ],
                    },
                )
                assert instrument.status_code == 201, instrument.text
                instrument_id = UUID(instrument.json()["id"])
                instrument_ids.append(instrument_id)
                crypto_ids[code] = instrument_id

            tbank_batch = await _upload_and_process(
                client,
                sessions,
                storage,
                portfolio_id=portfolio_id,
                account_id=account_ids["tbank"],
                provider="tbank_broker_xlsx",
                declared_format="xlsx",
                files=[
                    (
                        "file",
                        (
                            "synthetic-tbank.xlsx",
                            (
                                _FIXTURES / "tbank_broker_report_synthetic_v1.xlsx"
                            ).read_bytes(),
                            "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                        ),
                    )
                ],
            )
            alfa_batch = await _upload_and_process(
                client,
                sessions,
                storage,
                portfolio_id=portfolio_id,
                account_id=account_ids["alfa"],
                provider="alfa_broker_xml_import",
                declared_format="xml",
                files=[
                    (
                        "file",
                        (
                            "synthetic-alfa.xml",
                            (
                                _FIXTURES
                                / "alfa_broker_report_import_synthetic_v1.xml"
                            ).read_bytes(),
                            "application/xml",
                        ),
                    )
                ],
            )
            bybit_batch = await _upload_and_process(
                client,
                sessions,
                storage,
                portfolio_id=portfolio_id,
                account_id=account_ids["bybit"],
                provider="bybit_spot_csv_bundle",
                declared_format="csv",
                files=[
                    ("file", (name, (_FIXTURES / name).read_bytes(), "text/csv"))
                    for name in _BYBIT_FILES
                ],
            )

            assert await _exclude_warnings_and_confirm(client, tbank_batch) == 2
            assert await _exclude_warnings_and_confirm(client, alfa_batch) == 5
            assert await _exclude_warnings_and_confirm(client, bybit_batch) == 10

            preliminary = await client.post(
                f"/api/v1/portfolios/{portfolio_id}/positions/recalculate",
                json={
                    "as_of": "2026-04-01T00:00:00Z",
                    "cost_basis_method": "weighted_average",
                },
            )
            assert preliminary.status_code == 200, preliminary.text
            position_ids = {
                UUID(item["instrument_id"])
                for item in preliminary.json()["positions"]
            }
            instrument_ids.extend(position_ids - set(instrument_ids))
            instrument_list = await client.get(
                "/api/v1/instruments",
                params={"limit": 100, "offset": 0},
            )
            assert instrument_list.status_code == 200, instrument_list.text
            currencies = {
                UUID(item["id"]): item["currency"]
                for item in instrument_list.json()["items"]
            }

            observation_suffix = uuid4().int % 1_000_000
            observed_at = f"2026-03-31T00:00:00.{observation_suffix:06d}Z"
            known_prices = {
                fund_id: "1100",
                crypto_ids["SYNTH"]: "500",
                crypto_ids["USDT"]: "1",
            }
            prices = await client.post(
                "/api/v1/prices/batch",
                json={
                    "items": [
                        {
                            "instrument_id": str(instrument_id),
                            "price": known_prices.get(instrument_id, "1"),
                            "currency": currencies[instrument_id],
                            "observed_at": observed_at,
                        }
                        for instrument_id in sorted(position_ids, key=str)
                    ]
                },
            )
            assert prices.status_code == 201, prices.text
            market_price_ids = [UUID(item["id"]) for item in prices.json()["items"]]
            rates = await client.post(
                "/api/v1/fx-rates/batch",
                json={
                    "items": [
                        {
                            "base_currency": "USD",
                            "quote_currency": "RUB",
                            "rate": "100",
                            "observed_at": observed_at,
                        }
                    ]
                },
            )
            assert rates.status_code == 201, rates.text
            exchange_rate_ids = [UUID(item["id"]) for item in rates.json()["items"]]

            recalculated = await client.post(
                f"/api/v1/portfolios/{portfolio_id}/positions/recalculate",
                json={
                    "as_of": "2026-04-01T00:00:00Z",
                    "cost_basis_method": "weighted_average",
                },
            )
            assert recalculated.status_code == 200, recalculated.text
            assert recalculated.json()["operation_count"] == 17

            overview = await client.get(
                f"/api/v1/portfolios/{portfolio_id}/analytics/overview",
                params={"reporting_currency": "RUB"},
            )
            holdings = await client.get(
                f"/api/v1/portfolios/{portfolio_id}/analytics/holdings",
                params={"reporting_currency": "RUB"},
            )
            allocation = await client.get(
                f"/api/v1/portfolios/{portfolio_id}/allocation",
                params={"reporting_currency": "RUB"},
            )
            quality = await client.get(
                f"/api/v1/portfolios/{portfolio_id}/analytics/data-quality",
                params={"reporting_currency": "RUB"},
            )
            for response in (overview, holdings, allocation, quality):
                assert response.status_code == 200, response.text

            overview_body = overview.json()
            assert overview_body["snapshot_fresh"] is True
            assert overview_body["current_value"]["quality"] == "complete"
            assert overview_body["current_value"]["total_value"] is not None
            holding_total = sum(
                (
                    Decimal(item["market_value"])
                    for item in holdings.json()["items"]
                    if item["market_value"] is not None
                ),
                start=Decimal(0),
            )
            assert holding_total == Decimal(
                overview_body["current_value"]["known_value"]
            )
            assert {item["account_type"] for item in holdings.json()["items"]} == {
                "broker",
                "cex",
            }

            allocation_body = allocation.json()
            assert allocation_body["quality"] == "complete"
            assert sum(
                (
                    Decimal(item["actual_weight"])
                    for item in allocation_body["items"]
                    if item["actual_weight"] is not None
                ),
                start=Decimal(0),
            ) == Decimal(1)

            timeline_params = {
                "from": "2026-01-01",
                "to": "2026-04-02",
                "bucket": "month",
                "timezone": "Europe/Moscow",
            }
            timeline_bodies: dict[str, dict[str, object]] = {}
            for view in ("cash-flows", "income", "costs", "trading"):
                response = await client.get(
                    f"/api/v1/portfolios/{portfolio_id}/analytics/{view}",
                    params=timeline_params,
                )
                assert response.status_code == 200, response.text
                body = response.json()
                assert body["series_total"] == len(body["series"])
                assert len({item["key"] for item in body["series"]}) == len(
                    body["series"]
                )
                timeline_bodies[view] = body
            assert timeline_bodies["cash-flows"]["series"]
            assert timeline_bodies["income"]["series"]
            assert {
                item["unit_type"] for item in timeline_bodies["costs"]["series"]
            } == {"currency", "asset"}
            assert {
                item["unit_type"] for item in timeline_bodies["trading"]["series"]
            } == {"currency", "asset"}

            quality_body = quality.json()
            quality_codes = {item["code"] for item in quality_body["diagnostics"]}
            assert "import_history_period_limited" in quality_codes
            assert "cost_basis_unknown" in quality_codes
            assert "raw_data" not in quality.text
            assert "account_id" not in quality.text
            assert "instrument_id" not in quality.text

            benchmark_requests = {
                "overview": (
                    f"/api/v1/portfolios/{portfolio_id}/analytics/overview",
                    {"reporting_currency": "RUB"},
                ),
                "allocation": (
                    f"/api/v1/portfolios/{portfolio_id}/allocation",
                    {"reporting_currency": "RUB"},
                ),
                "trading": (
                    f"/api/v1/portfolios/{portfolio_id}/analytics/trading",
                    timeline_params,
                ),
                "data_quality": (
                    f"/api/v1/portfolios/{portfolio_id}/analytics/data-quality",
                    {"reporting_currency": "RUB"},
                ),
            }
            benchmark_ms: dict[str, float] = {}
            for name, (path, params) in benchmark_requests.items():
                warmup = await client.get(path, params=params)
                assert warmup.status_code == 200, warmup.text
                samples: list[float] = []
                for _ in range(7):
                    started = perf_counter()
                    measured = await client.get(path, params=params)
                    samples.append((perf_counter() - started) * 1000)
                    assert measured.status_code == 200, measured.text
                benchmark_ms[name] = median(samples)
            with capsys.disabled():
                print(
                    "phase03 synthetic analytics median ms: "
                    + ", ".join(
                        f"{name}={value:.3f}"
                        for name, value in sorted(benchmark_ms.items())
                    )
                )
    finally:
        async with sessions.begin() as session:
            if portfolio_id is not None:
                await session.execute(
                    delete(CalculationSnapshotModel).where(
                        CalculationSnapshotModel.portfolio_id == portfolio_id
                    )
                )
            if market_price_ids:
                await session.execute(
                    delete(MarketPriceModel).where(
                        MarketPriceModel.id.in_(market_price_ids)
                    )
                )
            if exchange_rate_ids:
                await session.execute(
                    delete(ExchangeRateModel).where(
                        ExchangeRateModel.id.in_(exchange_rate_ids)
                    )
                )
            if portfolio_id is not None:
                storage_keys = list(
                    await session.scalars(
                        select(ImportBatchFileModel.storage_key)
                        .join(ImportBatchModel)
                        .where(ImportBatchModel.portfolio_id == portfolio_id)
                    )
                )
                scoped_batches = select(ImportBatchModel.id).where(
                    ImportBatchModel.portfolio_id == portfolio_id
                )
                await session.execute(
                    update(ImportRowModel)
                    .where(ImportRowModel.batch_id.in_(scoped_batches))
                    .values(matched_operation_id=None)
                )
                await session.execute(
                    delete(OperationModel).where(
                        OperationModel.import_batch_id.in_(scoped_batches)
                    )
                )
                await session.execute(
                    delete(ImportBatchModel).where(
                        ImportBatchModel.portfolio_id == portfolio_id
                    )
                )
                await session.execute(
                    delete(PortfolioModel).where(PortfolioModel.id == portfolio_id)
                )
            if instrument_ids:
                await session.execute(
                    delete(InstrumentModel).where(InstrumentModel.id.in_(instrument_ids))
                )
        for key in storage_keys:
            storage.delete(key)
        application.dependency_overrides.clear()
        await engine.dispose()
