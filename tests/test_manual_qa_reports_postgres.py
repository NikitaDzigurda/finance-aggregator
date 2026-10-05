"""End-to-end financial checks for the reports offered for manual verification."""

import json
import os
from collections.abc import AsyncIterator
from decimal import Decimal
from pathlib import Path
from uuid import UUID

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import delete, select, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from apps.api.main import create_app
from imports.adapters import get_adapter_registry
from imports.models import ImportBatchModel, ImportRowModel
from imports.processor import process_import_batch
from imports.storage import LocalObjectStorage, get_object_storage
from instruments.models import InstrumentIdentifierModel, InstrumentModel
from operations.models import OperationModel
from portfolios.models import PortfolioModel
from shared.database import get_db_session

FIXTURES = Path(__file__).parents[1] / "docs/fixtures/manual_qa"


@pytest.mark.postgres
@pytest.mark.asyncio
async def test_manual_qa_upload_confirm_balances_dedup_and_rollback(tmp_path: Path):
    url = os.getenv("FINANCE_TEST_DATABASE_URL")
    if not url:
        pytest.skip("dedicated test database required")
    engine = create_async_engine(url)
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    storage = LocalObjectStorage(tmp_path / "reports", max_file_size_bytes=1024 * 1024)
    app = create_app()

    async def db() -> AsyncIterator[AsyncSession]:
        async with sessions() as session:
            yield session

    app.dependency_overrides[get_db_session] = db
    app.dependency_overrides[get_object_storage] = lambda: storage
    expected = json.loads((FIXTURES / "expected.json").read_text())
    portfolio_ids = []
    async with sessions() as session:
        original_instruments = set(await session.scalars(select(InstrumentModel.id)))
    try:
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            for key, provider, extension in (
                ("tbank", "tbank_broker_xlsx", "xlsx"),
                ("alfa", "alfa_broker_xml_import", "xml"),
                ("bybit", "bybit_spot_csv_bundle", "csv"),
            ):
                p = await client.post(
                    "/api/v1/portfolios", json={"name": f"QA reports {key}", "base_currency": "RUB"}
                )
                assert p.status_code == 201, p.text
                pid = p.json()["id"]
                portfolio_ids.append(UUID(pid))
                account = await client.post(
                    f"/api/v1/portfolios/{pid}/accounts",
                    json={"name": key, "account_type": "cex" if key == "bybit" else "broker"},
                )
                assert account.status_code == 201, account.text
                form = dict(
                    portfolio_id=pid,
                    account_id=account.json()["id"],
                    source_provider=provider,
                    declared_format=extension,
                )
                files = [
                    ("file", (f.name, f.read_bytes(), "application/octet-stream"))
                    for f in sorted(FIXTURES.glob(f"*.{extension}"))
                ]
                uploaded = await client.post("/api/v1/imports", data=form, files=files)
                assert uploaded.status_code == 201, uploaded.text
                bid = uploaded.json()["batch"]["id"]
                await process_import_batch(
                    sessions, storage, get_adapter_registry(), batch_id=UUID(bid)
                )
                preview = (await client.get(f"/api/v1/imports/{bid}/preview")).json()
                assert preview["status"] == "ready_to_commit", preview
                assert preview["error_rows"] == preview["warning_rows"] == 0, preview
                if key != "bybit":
                    assert preview["reconciliation_status"] == "matched", preview
                confirm = await client.post(f"/api/v1/imports/{bid}/confirm")
                assert confirm.status_code == 200, confirm.text
                assert (await client.post(f"/api/v1/imports/{bid}/confirm")).json()["idempotent"]
                duplicate = await client.post("/api/v1/imports", data=form, files=files)
                assert duplicate.json()["duplicate"]
                result = await client.post(
                    f"/api/v1/portfolios/{pid}/positions/recalculate",
                    json={"as_of": "2026-04-01T00:00:00Z"},
                )
                assert result.status_code == 200, result.text
                snapshot = result.json()
                assert "negative_position" not in {d["code"] for d in snapshot["diagnostics"]}
                async with sessions() as session:
                    identifiers = {
                        ident.value: str(ident.instrument_id)
                        for ident in await session.scalars(select(InstrumentIdentifierModel))
                    }
                for code, quantity in expected[key]["quantities"].items():
                    pos = next(
                        x for x in snapshot["positions"] if x["instrument_id"] == identifiers[code]
                    )
                    assert Decimal(pos["quantity"]) == Decimal(quantity), (key, code, pos)
                if key != "bybit":
                    assert Decimal(snapshot["cash_balances"][0]["amount"]) == Decimal(
                        expected[key]["cash_RUB"]
                    )
                    assert sum(Decimal(x["cost_basis"]) for x in snapshot["positions"]) == Decimal(
                        "18600"
                    )
                    assert sum(
                        Decimal(x["realised_pnl"]) for x in snapshot["currency_metrics"]
                    ) == Decimal("750")
                rollback = await client.post(f"/api/v1/imports/{bid}/rollback")
                assert rollback.status_code == 200, rollback.text
                assert (await client.post(f"/api/v1/imports/{bid}/rollback")).json()["idempotent"]
                empty = await client.post(
                    f"/api/v1/portfolios/{pid}/positions/recalculate",
                    json={"as_of": "2026-04-01T00:00:00Z"},
                )
                assert empty.json()["operation_count"] == 0
    finally:
        async with sessions.begin() as session:
            batches = select(ImportBatchModel.id).where(
                ImportBatchModel.portfolio_id.in_(portfolio_ids)
            )
            await session.execute(
                update(ImportRowModel)
                .where(ImportRowModel.batch_id.in_(batches))
                .values(matched_operation_id=None)
            )
            await session.execute(
                delete(OperationModel).where(OperationModel.import_batch_id.in_(batches))
            )
            await session.execute(
                delete(ImportBatchModel).where(ImportBatchModel.portfolio_id.in_(portfolio_ids))
            )
            await session.execute(
                delete(PortfolioModel).where(PortfolioModel.id.in_(portfolio_ids))
            )
            await session.execute(
                delete(InstrumentModel).where(InstrumentModel.id.not_in(original_instruments))
            )
        await engine.dispose()
