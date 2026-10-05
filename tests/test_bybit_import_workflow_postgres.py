import os
from collections.abc import AsyncIterator
from decimal import Decimal
from pathlib import Path
from uuid import UUID, uuid4

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import delete, select, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from apps.api.main import create_app
from imports.adapters import get_adapter_registry
from imports.models import ImportBatchFileModel, ImportBatchModel, ImportRowModel
from imports.processor import process_import_batch
from imports.storage import LocalObjectStorage, get_object_storage
from instruments.models import (
    InstrumentIdentifierModel,
    InstrumentIdentifierType,
    InstrumentModel,
)
from operations.models import OperationModel
from portfolios.models import PortfolioModel
from shared.database import get_db_session

_FIXTURES = Path(__file__).parents[1] / "docs" / "fixtures"
_NAMES = (
    "bybit_spot_trade_history_synthetic_v1.csv",
    "bybit_uta_asset_change_details_synthetic_v1.csv",
    "bybit_funding_asset_change_details_synthetic_v1.csv",
    "bybit_withdraw_deposit_history_synthetic_v1.csv",
)


def _files(names: tuple[str, ...] = _NAMES) -> list[tuple[str, tuple[str, bytes, str]]]:
    return [
        ("file", (name, (_FIXTURES / name).read_bytes(), "text/csv")) for name in names
    ]


@pytest.mark.postgres
@pytest.mark.asyncio
async def test_bybit_bundle_confirm_recalculate_duplicate_and_rollback(tmp_path: Path) -> None:
    database_url = os.getenv("FINANCE_TEST_DATABASE_URL")
    if database_url is None:
        pytest.skip("FINANCE_TEST_DATABASE_URL is not configured")

    engine = create_async_engine(database_url)
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    storage = LocalObjectStorage(tmp_path / "bybit-imports", max_file_size_bytes=128 * 1024)

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
    storage_keys: list[str] = []
    try:
        async with AsyncClient(
            transport=ASGITransport(app=application), base_url="http://test"
        ) as client:
            portfolio = await client.post(
                "/api/v1/portfolios",
                json={"name": f"Bybit {uuid4().hex[:8]}", "base_currency": "USD"},
            )
            assert portfolio.status_code == 201
            portfolio_id = UUID(portfolio.json()["id"])
            account = await client.post(
                f"/api/v1/portfolios/{portfolio_id}/accounts",
                json={"name": "Synthetic Bybit Spot", "account_type": "cex"},
            )
            assert account.status_code == 201, account.text
            account_id = account.json()["id"]

            for code in ("SYNTH", "USDT"):
                instrument = await client.post(
                    "/api/v1/instruments",
                    json={
                        "name": f"Synthetic {code}",
                        "instrument_type": "crypto_asset",
                        "currency": "USD",
                        "identifiers": [
                            {"identifier_type": "crypto_asset_code", "value": code}
                        ],
                    },
                )
                assert instrument.status_code == 201, instrument.text
                instrument_ids.append(UUID(instrument.json()["id"]))

            form = {
                "portfolio_id": str(portfolio_id),
                "account_id": account_id,
                "source_provider": "bybit_spot_csv_bundle",
                "declared_format": "csv",
            }
            partial_failure = await client.post(
                "/api/v1/imports",
                data=form,
                files=[
                    _files()[0],
                    ("file", ("invalid.csv", b"invalid\x00csv", "text/csv")),
                ],
            )
            assert partial_failure.status_code == 422
            assert list((tmp_path / "bybit-imports").rglob("*.csv")) == []

            upload = await client.post("/api/v1/imports", data=form, files=_files())
            assert upload.status_code == 201, upload.text
            body = upload.json()["batch"]
            assert len(body["files"]) == 4
            assert {item["sha256"] for item in body["files"]}
            batch_id = UUID(body["id"])

            duplicate = await client.post(
                "/api/v1/imports", data=form, files=_files(tuple(reversed(_NAMES)))
            )
            assert duplicate.status_code == 200, duplicate.text
            assert duplicate.json()["duplicate"] is True
            assert duplicate.json()["batch"]["id"] == str(batch_id)
            assert len(list((tmp_path / "bybit-imports").rglob("*.csv"))) == 4

            await process_import_batch(
                sessions, storage, get_adapter_registry(), batch_id=batch_id
            )
            preview_response = await client.get(f"/api/v1/imports/{batch_id}/preview")
            assert preview_response.status_code == 200, preview_response.text
            preview = preview_response.json()
            assert preview["status"] == "ready_to_commit"
            assert preview["ready_rows"] == 10, {
                "counts": {
                    key: preview[key]
                    for key in (
                        "ready_rows",
                        "warning_rows",
                        "error_rows",
                        "excluded_rows",
                    )
                },
                "diagnostics": preview["summary"]["diagnostic_counts"],
            }
            assert preview["warning_rows"] == 0
            assert preview["excluded_rows"] == 4
            assert preview["error_rows"] == 0
            assert preview["summary"]["operation_counts"] == {
                "balance_adjustment": 4,
                "crypto_trade": 2,
                "crypto_transfer": 1,
                "fee": 3,
            }
            source_file_ids = {row["source_file_id"] for row in preview["items"]}
            assert None not in source_file_ids
            assert source_file_ids <= {item["id"] for item in body["files"]}

            confirm = await client.post(f"/api/v1/imports/{batch_id}/confirm")
            assert confirm.status_code == 200, confirm.text
            assert confirm.json()["operation_count"] == 10
            assert confirm.json()["idempotent"] is False
            repeated = await client.post(f"/api/v1/imports/{batch_id}/confirm")
            assert repeated.json()["idempotent"] is True

            recalculation = await client.post(
                f"/api/v1/portfolios/{portfolio_id}/positions/recalculate",
                json={
                    "as_of": "2026-04-01T00:00:00Z",
                    "cost_basis_method": "weighted_average",
                },
            )
            assert recalculation.status_code == 200, recalculation.text
            positions = {
                item["instrument_id"]: Decimal(item["quantity"])
                for item in recalculation.json()["positions"]
            }
            code_to_id = {
                code: str(value)
                for code, value in zip(("SYNTH", "USDT"), instrument_ids, strict=True)
            }
            assert positions[code_to_id["SYNTH"]] == Decimal("2.995")
            assert positions[code_to_id["USDT"]] == Decimal("-39.146")

            async with sessions() as session:
                bonus_id = await session.scalar(
                    select(InstrumentModel.id)
                    .join(InstrumentIdentifierModel)
                    .where(
                        InstrumentIdentifierModel.identifier_type
                        == InstrumentIdentifierType.CRYPTO_ASSET_CODE,
                        InstrumentIdentifierModel.value == "BONUS",
                    )
                )
            assert bonus_id is not None
            instrument_ids.append(bonus_id)
            assert positions[str(bonus_id)] == Decimal("1")

            rollback = await client.post(f"/api/v1/imports/{batch_id}/rollback")
            assert rollback.status_code == 200, rollback.text
            assert rollback.json()["rolled_back_operations"] == 10
            assert (await client.post(f"/api/v1/imports/{batch_id}/rollback")).json()[
                "idempotent"
            ] is True
    finally:
        async with sessions.begin() as session:
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
