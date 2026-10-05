import os
from collections import Counter
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
from imports.jobs import claim_next_job, finish_job
from imports.models import ImportBatchModel, ImportRowModel
from imports.processor import process_import_batch
from imports.storage import LocalObjectStorage, get_object_storage
from instruments.models import InstrumentModel
from operations.models import OperationModel
from portfolios.models import PortfolioModel
from shared.database import get_db_session

_FIXTURE = (
    Path(__file__).parents[1]
    / "docs"
    / "fixtures"
    / "alfa_broker_report_import_synthetic_v1.xml"
)


async def _upload_and_process(
    client: AsyncClient,
    sessions: async_sessionmaker[AsyncSession],
    storage: LocalObjectStorage,
    *,
    portfolio_id: UUID,
    account_id: str,
    content: bytes | None = None,
) -> UUID:
    response = await client.post(
        "/api/v1/imports",
        data={
            "portfolio_id": str(portfolio_id),
            "account_id": account_id,
            "source_provider": "alfa_broker_xml_import",
            "declared_format": "xml",
        },
        files={
            "file": (
                "synthetic-alfa.xml",
                _FIXTURE.read_bytes() if content is None else content,
                "application/xml",
            )
        },
    )
    assert response.status_code == 201, response.text
    batch_id = UUID(response.json()["batch"]["id"])
    worker_id = f"alfa-test-{uuid4()}"
    async with sessions() as session:
        job = await claim_next_job(session, worker_id=worker_id)
    assert job is not None
    assert job.batch_id == batch_id
    await process_import_batch(
        sessions,
        storage,
        get_adapter_registry(),
        batch_id=batch_id,
    )
    async with sessions() as session:
        await finish_job(session, job_id=job.id, worker_id=worker_id, succeeded=True)
    return batch_id


@pytest.mark.postgres
@pytest.mark.asyncio
async def test_alfa_full_import_recalculate_and_rollback(tmp_path: Path) -> None:
    database_url = os.getenv("FINANCE_TEST_DATABASE_URL")
    if database_url is None:
        pytest.skip("FINANCE_TEST_DATABASE_URL is not configured")

    engine = create_async_engine(database_url)
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    storage = LocalObjectStorage(tmp_path / "alfa-imports", max_file_size_bytes=128 * 1024)

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
    transport = ASGITransport(app=application)
    portfolio_id: UUID | None = None
    batch_id: UUID | None = None
    storage_keys: list[str] = []

    try:
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            portfolio_response = await client.post(
                "/api/v1/portfolios",
                json={"name": f"Alfa {uuid4().hex[:8]}", "base_currency": "RUB"},
            )
            assert portfolio_response.status_code == 201
            portfolio_id = UUID(portfolio_response.json()["id"])
            account_response = await client.post(
                f"/api/v1/portfolios/{portfolio_id}/accounts",
                json={"name": "Synthetic Alfa broker", "account_type": "broker"},
            )
            assert account_response.status_code == 201
            account_id = account_response.json()["id"]
            instrument_response = await client.post(
                "/api/v1/instruments",
                json={
                    "name": "Synthetic Bond Fund",
                    "instrument_type": "fund",
                    "currency": "RUB",
                    "identifiers": [
                        {
                            "identifier_type": "provider_code",
                            "value": "SYNBOND",
                            "provider": "alfa_broker_xml_import",
                        }
                    ],
                },
            )
            assert instrument_response.status_code == 201, instrument_response.text
            batch_id = await _upload_and_process(
                client,
                sessions,
                storage,
                portfolio_id=portfolio_id,
                account_id=account_id,
            )
            status_response = await client.get(f"/api/v1/imports/{batch_id}")
            status_body = status_response.json()["batch"]
            assert status_body["status"] == "ready_to_commit"
            assert status_body["reporting_period_start"] == "2026-01-01"
            assert status_body["reporting_period_end"] == "2026-01-31"
            assert status_body["completeness"] == "period_ledger"
            assert status_body["reconciliation_status"] == "matched"
            assert status_body["ready_rows"] == 5
            assert status_body["warning_rows"] == 0
            assert status_body["excluded_rows"] == 6
            assert status_body["error_rows"] == 0

            preview_response = await client.get(f"/api/v1/imports/{batch_id}/preview")
            assert preview_response.status_code == 200
            preview = preview_response.json()
            assert preview["total_rows"] == 11
            assert preview["summary"]["operation_counts"] == {
                "balance_adjustment": 1,
                "cash_movement": 1,
                "fee": 1,
                "income": 1,
                "trade": 1,
            }
            assert preview["reconciliation_summary"]["mismatch_count"] == 0
            assert preview["reconciliation_summary"]["diagnostics"] == []
            ready_status = await client.get(f"/api/v1/imports/{batch_id}")
            assert ready_status.json()["batch"]["status"] == "ready_to_commit"
            confirm = await client.post(f"/api/v1/imports/{batch_id}/confirm")
            assert confirm.status_code == 200, confirm.text
            assert confirm.json()["operation_count"] == 5
            assert confirm.json()["idempotent"] is False
            repeated_confirm = await client.post(f"/api/v1/imports/{batch_id}/confirm")
            assert repeated_confirm.json()["idempotent"] is True

            operations_response = await client.get(
                "/api/v1/operations",
                params={"account_id": account_id, "limit": 20},
            )
            assert operations_response.status_code == 200
            operations = operations_response.json()["items"]
            assert Counter(item["operation_type"] for item in operations) == {
                "balance_adjustment": 1,
                "trade": 1,
                "fee": 1,
                "cash_movement": 1,
                "income": 1,
            }
            assert {item["source"]["type"] for item in operations} == {"xml_import"}
            assert {
                item["source"]["source_operation_id"]
                for item in operations
                if item["operation_type"] in {"trade", "fee"}
            } == {"1000001"}

            recalculation = await client.post(
                f"/api/v1/portfolios/{portfolio_id}/positions/recalculate",
                json={
                    "as_of": "2026-02-01T00:00:00Z",
                    "cost_basis_method": "weighted_average",
                },
            )
            assert recalculation.status_code == 200, recalculation.text
            result = recalculation.json()
            assert result["operation_count"] == 5
            assert {Decimal(item["quantity"]) for item in result["positions"]} == {
                Decimal("3"),
                Decimal("10"),
            }
            assert len(result["positions"]) == 2
            assert {
                None if item["realised_pnl"] is None else Decimal(item["realised_pnl"])
                for item in result["positions"]
            } == {None, Decimal("0")}
            assert {
                item["currency"]: Decimal(item["amount"])
                for item in result["cash_balances"]
            } == {"RUB": Decimal("240")}
            rub_metrics = next(
                item for item in result["currency_metrics"] if item["currency"] == "RUB"
            )
            assert Decimal(rub_metrics["fees"]) == Decimal("10")
            assert Decimal(rub_metrics["income"]) == Decimal("250")

            overlap_content = _FIXTURE.read_bytes().replace(
                b"<sys_name>SYNTHETIC_FIXTURE</sys_name>",
                b"<sys_name>SYNTHETIC_OVERLAP</sys_name>",
                1,
            )
            overlap_batch = await _upload_and_process(
                client,
                sessions,
                storage,
                portfolio_id=portfolio_id,
                account_id=account_id,
                content=overlap_content,
            )
            overlap_preview = (
                await client.get(f"/api/v1/imports/{overlap_batch}/preview")
            ).json()
            duplicate_types = Counter(
                row["normalized_candidate"]["operation_type"]
                for row in overlap_preview["items"]
                if row["status"] == "duplicate"
            )
            assert duplicate_types == {
                "balance_adjustment": 1,
                "trade": 1,
                "fee": 1,
                "cash_movement": 1,
                "income": 1,
            }
            overlap_confirm = await client.post(
                f"/api/v1/imports/{overlap_batch}/confirm"
            )
            assert overlap_confirm.status_code == 409
            operations_after_overlap = await client.get(
                "/api/v1/operations",
                params={"account_id": account_id, "limit": 20},
            )
            assert len(operations_after_overlap.json()["items"]) == 5

            rollback = await client.post(f"/api/v1/imports/{batch_id}/rollback")
            assert rollback.status_code == 200, rollback.text
            assert rollback.json()["rolled_back_operations"] == 5
            assert rollback.json()["idempotent"] is False
            repeated_rollback = await client.post(f"/api/v1/imports/{batch_id}/rollback")
            assert repeated_rollback.json()["idempotent"] is True
    finally:
        async with sessions.begin() as session:
            if portfolio_id is not None:
                storage_keys = list(
                    await session.scalars(
                        select(ImportBatchModel.storage_key).where(
                            ImportBatchModel.portfolio_id == portfolio_id
                        )
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
            await session.execute(
                delete(InstrumentModel).where(
                    InstrumentModel.name.in_(("Synthetic Bond Fund", "Synthetic Share"))
                )
            )
        for storage_key in storage_keys:
            storage.delete(storage_key)
        application.dependency_overrides.clear()
        await engine.dispose()
