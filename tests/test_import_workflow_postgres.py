import os
from collections.abc import AsyncIterator
from pathlib import Path
from uuid import UUID, uuid4

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import delete, select, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from apps.api.main import create_app
from imports.adapters import get_adapter_registry
from imports.deduplication import import_fingerprint
from imports.jobs import claim_next_job, finish_job
from imports.models import (
    ImportBatchModel,
    ImportFileFormat,
    ImportRowModel,
    ImportRowStatus,
    ImportStatus,
)
from imports.processor import process_import_batch
from imports.storage import LocalObjectStorage, get_object_storage
from instruments.models import InstrumentModel
from operations.models import OperationModel
from portfolios.models import PortfolioModel
from shared.database import get_db_session

_HEADER = (
    "Date,Type,Symbol,ISIN,Quantity,Price,Amount,Currency,Fee,FeeCurrency,Tax,"
    "AccruedInterest,Exchange,ExternalId,Note\n"
)


async def _upload_and_process(
    client: AsyncClient,
    sessions: async_sessionmaker[AsyncSession],
    storage: LocalObjectStorage,
    *,
    portfolio_id: UUID,
    account_id: str,
    filename: str,
    content: bytes,
) -> UUID:
    response = await client.post(
        "/api/v1/imports",
        data={
            "portfolio_id": str(portfolio_id),
            "account_id": account_id,
            "source_provider": "universal_broker",
            "declared_format": "csv",
        },
        files={"file": (filename, content, "text/csv")},
    )
    assert response.status_code == 201, response.text
    batch_id = UUID(response.json()["batch"]["id"])
    worker_id = f"test-worker-{uuid4()}"
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
        await finish_job(
            session,
            job_id=job.id,
            worker_id=worker_id,
            succeeded=True,
        )
    return batch_id


@pytest.mark.postgres
@pytest.mark.asyncio
async def test_confirm_deduplicate_and_rollback_are_idempotent_and_scoped(
    tmp_path: Path,
) -> None:
    database_url = os.getenv("FINANCE_TEST_DATABASE_URL")
    if database_url is None:
        pytest.skip("FINANCE_TEST_DATABASE_URL is not configured")

    engine = create_async_engine(database_url)
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    storage = LocalObjectStorage(tmp_path / "workflow-imports", max_file_size_bytes=4096)

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
    suffix = uuid4().hex[:8].upper()
    portfolio_id: UUID | None = None
    instrument_id: UUID | None = None
    account_id: str | None = None
    batch_ids: list[UUID] = []

    try:
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            portfolio = await client.post(
                "/api/v1/portfolios",
                json={"name": f"Workflow {suffix}", "base_currency": "USD"},
            )
            assert portfolio.status_code == 201
            portfolio_id = UUID(portfolio.json()["id"])
            account = await client.post(
                f"/api/v1/portfolios/{portfolio_id}/accounts",
                json={"name": "Synthetic broker", "account_type": "broker"},
            )
            assert account.status_code == 201
            account_id = account.json()["id"]
            isin = f"US{suffix}X1"
            instrument = await client.post(
                "/api/v1/instruments",
                json={
                    "name": "Synthetic workflow equity",
                    "instrument_type": "stock",
                    "currency": "USD",
                    "identifiers": [
                        {"identifier_type": "isin", "value": isin},
                        {
                            "identifier_type": "ticker",
                            "value": f"W{suffix}",
                            "exchange": "XNAS",
                        },
                    ],
                },
            )
            assert instrument.status_code == 201, instrument.text
            instrument_id = UUID(instrument.json()["id"])

            manual = await client.post(
                "/api/v1/operations",
                json={
                    "portfolio_id": str(portfolio_id),
                    "account_id": account_id,
                    "occurred_at": "2026-08-01T00:00:00Z",
                    "time_precision": "date",
                    "operation_type": "cash_movement",
                    "payload": {
                        "direction": "deposit",
                        "amount": "5000.00",
                        "currency": "USD",
                    },
                },
            )
            assert manual.status_code == 201, manual.text
            manual_id = manual.json()["id"]

            batch_a = await _upload_and_process(
                client,
                sessions,
                storage,
                portfolio_id=portfolio_id,
                account_id=account_id,
                filename="batch-a.csv",
                content=(
                    _HEADER
                    + f"2026-08-03,Buy,W{suffix},{isin},10,125.50,,USD,1.25,USD,"
                    "2.50,0,XNAS,EXT-A,Synthetic batch A\n"
                ).encode(),
            )
            batch_ids.append(batch_a)
            confirm_a = await client.post(f"/api/v1/imports/{batch_a}/confirm")
            assert confirm_a.status_code == 200, confirm_a.text
            assert confirm_a.json()["operation_count"] == 3
            assert confirm_a.json()["idempotent"] is False
            operation_ids_a = set(confirm_a.json()["operation_ids"])

            confirm_a_again = await client.post(f"/api/v1/imports/{batch_a}/confirm")
            assert confirm_a_again.status_code == 200
            assert confirm_a_again.json()["idempotent"] is True
            assert set(confirm_a_again.json()["operation_ids"]) == operation_ids_a

            batch_b = await _upload_and_process(
                client,
                sessions,
                storage,
                portfolio_id=portfolio_id,
                account_id=account_id,
                filename="batch-b.csv",
                content=(
                    _HEADER
                    + "2026-08-04,CashIn,,,,,750.00,USD,,USD,0,0,,EXT-B,"
                    "Synthetic batch B\n"
                ).encode(),
            )
            batch_ids.append(batch_b)
            confirm_b = await client.post(f"/api/v1/imports/{batch_b}/confirm")
            assert confirm_b.status_code == 200, confirm_b.text
            operation_id_b = confirm_b.json()["operation_ids"][0]

            batch_duplicate = await _upload_and_process(
                client,
                sessions,
                storage,
                portfolio_id=portfolio_id,
                account_id=account_id,
                filename="batch-duplicate.csv",
                content=(
                    _HEADER
                    + f"2026-08-05,Buy,W{suffix},{isin},11,999.99,,USD,9.99,USD,"
                    "8.88,0,XNAS,EXT-A,Changed values with the same external ID\n"
                ).encode(),
            )
            batch_ids.append(batch_duplicate)
            duplicate_preview = await client.get(
                f"/api/v1/imports/{batch_duplicate}/preview"
            )
            assert duplicate_preview.status_code == 200
            assert duplicate_preview.json()["status"] == "awaiting_review"
            assert duplicate_preview.json()["duplicate_rows"] == 3
            assert {
                item["warnings"][0]["code"] for item in duplicate_preview.json()["items"]
            } == {"import_duplicate"}
            for item in duplicate_preview.json()["items"]:
                allowed = await client.patch(
                    f"/api/v1/imports/{batch_duplicate}/rows/{item['id']}",
                    json={
                        "action": "allow_duplicate",
                        "note": "Synthetic explicit duplicate acceptance",
                    },
                )
                assert allowed.status_code == 200, allowed.text
                assert allowed.json()["status"] == "ready"
            confirm_duplicate = await client.post(
                f"/api/v1/imports/{batch_duplicate}/confirm"
            )
            assert confirm_duplicate.status_code == 200, confirm_duplicate.text
            operation_ids_duplicate = set(confirm_duplicate.json()["operation_ids"])
            assert len(operation_ids_duplicate) == 3

            rollback_a = await client.post(f"/api/v1/imports/{batch_a}/rollback")
            assert rollback_a.status_code == 200, rollback_a.text
            assert rollback_a.json()["rolled_back_operations"] == 3
            assert rollback_a.json()["idempotent"] is False
            rollback_a_again = await client.post(f"/api/v1/imports/{batch_a}/rollback")
            assert rollback_a_again.status_code == 200
            assert rollback_a_again.json()["idempotent"] is True
            assert rollback_a_again.json()["rolled_back_operations"] == 3

            operations = await client.get(
                "/api/v1/operations",
                params={"account_id": account_id, "limit": 50},
            )
            assert operations.status_code == 200
            remaining_ids = {item["id"] for item in operations.json()["items"]}
            assert remaining_ids == {
                manual_id,
                operation_id_b,
                *operation_ids_duplicate,
            }
            assert remaining_ids.isdisjoint(operation_ids_a)
            batch_b_status = await client.get(f"/api/v1/imports/{batch_b}")
            assert batch_b_status.json()["batch"]["status"] == "committed"
    finally:
        storage_keys: list[str] = []
        async with sessions.begin() as session:
            if batch_ids:
                storage_keys = list(
                    await session.scalars(
                        select(ImportBatchModel.storage_key).where(
                            ImportBatchModel.id.in_(batch_ids)
                        )
                    )
                )
                await session.execute(
                    update(ImportRowModel)
                    .where(ImportRowModel.batch_id.in_(batch_ids))
                    .values(matched_operation_id=None)
                )
                await session.execute(
                    delete(OperationModel).where(OperationModel.import_batch_id.in_(batch_ids))
                )
                await session.execute(
                    delete(ImportBatchModel).where(ImportBatchModel.id.in_(batch_ids))
                )
            if portfolio_id is not None:
                await session.execute(
                    delete(OperationModel).where(OperationModel.portfolio_id == portfolio_id)
                )
                await session.execute(
                    delete(PortfolioModel).where(PortfolioModel.id == portfolio_id)
                )
            if instrument_id is not None:
                await session.execute(
                    delete(InstrumentModel).where(InstrumentModel.id == instrument_id)
                )
        for storage_key in storage_keys:
            storage.delete(storage_key)
        application.dependency_overrides.clear()
        await engine.dispose()


@pytest.mark.postgres
@pytest.mark.asyncio
async def test_confirm_rolls_back_all_operations_on_database_failure() -> None:
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
    transport = ASGITransport(app=application)
    suffix = uuid4().hex[:8].upper()
    portfolio_id: UUID | None = None
    instrument_id: UUID | None = None
    batch_id: UUID | None = None

    try:
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            portfolio = await client.post(
                "/api/v1/portfolios",
                json={"name": f"Atomic {suffix}", "base_currency": "USD"},
            )
            portfolio_id = UUID(portfolio.json()["id"])
            account = await client.post(
                f"/api/v1/portfolios/{portfolio_id}/accounts",
                json={"name": "Synthetic broker", "account_type": "broker"},
            )
            account_id = UUID(account.json()["id"])
            instrument = await client.post(
                "/api/v1/instruments",
                json={
                    "name": "Synthetic atomic equity",
                    "instrument_type": "stock",
                    "currency": "USD",
                    "identifiers": [
                        {"identifier_type": "isin", "value": f"US{suffix}Y1"}
                    ],
                },
            )
            instrument_id = UUID(instrument.json()["id"])
            missing_instrument_id = uuid4()
            candidates = [
                {
                    "operation_type": "trade",
                    "occurred_at": "2026-08-03T00:00:00Z",
                    "time_precision": "date",
                    "source_operation_id": f"ATOMIC-{index}",
                    "payload": {
                        "side": "buy",
                        "instrument_id": str(candidate_instrument_id),
                        "quantity": "1",
                        "price": "100.00",
                        "price_currency": "USD",
                    },
                }
                for index, candidate_instrument_id in enumerate(
                    [instrument_id, missing_instrument_id],
                    start=1,
                )
            ]
            batch_id = uuid4()
            async with sessions.begin() as session:
                batch = ImportBatchModel(
                    id=batch_id,
                    portfolio_id=portfolio_id,
                    account_id=account_id,
                    source_provider="universal_broker",
                    declared_format=ImportFileFormat.CSV,
                    detected_format="universal_broker",
                    detected_version="1.0",
                    original_filename="synthetic-atomic.csv",
                    storage_key=f"atomic/{batch_id}.csv",
                    file_size_bytes=1,
                    sha256=uuid4().hex + uuid4().hex,
                    status=ImportStatus.READY_TO_COMMIT,
                    total_rows=2,
                    ready_rows=2,
                )
                session.add(batch)
                for sequence_number, candidate in enumerate(candidates, start=1):
                    session.add(
                        ImportRowModel(
                            batch_id=batch_id,
                            sequence_number=sequence_number,
                            source_row_number=sequence_number + 1,
                            raw_data={"synthetic": True},
                            normalized_candidate=candidate,
                            status=ImportRowStatus.READY,
                            fingerprint=import_fingerprint(
                                account_id=account_id,
                                source_provider="universal_broker",
                                candidate=candidate,
                            ),
                        )
                    )

            confirm = await client.post(f"/api/v1/imports/{batch_id}/confirm")
            assert confirm.status_code == 409, confirm.text
            assert confirm.json()["error"]["code"] == "import_confirm_conflict"

        async with sessions() as session:
            operation_count = len(
                list(
                    await session.scalars(
                        select(OperationModel.id).where(
                            OperationModel.import_batch_id == batch_id
                        )
                    )
                )
            )
            stored_batch = await session.get(ImportBatchModel, batch_id)
            stored_rows = list(
                await session.scalars(
                    select(ImportRowModel).where(ImportRowModel.batch_id == batch_id)
                )
            )
            assert operation_count == 0
            assert stored_batch is not None
            assert stored_batch.status is ImportStatus.READY_TO_COMMIT
            assert all(row.matched_operation_id is None for row in stored_rows)
            assert all(row.status is ImportRowStatus.READY for row in stored_rows)
    finally:
        async with sessions.begin() as session:
            if batch_id is not None:
                await session.execute(
                    delete(ImportBatchModel).where(ImportBatchModel.id == batch_id)
                )
            if portfolio_id is not None:
                await session.execute(
                    delete(PortfolioModel).where(PortfolioModel.id == portfolio_id)
                )
            if instrument_id is not None:
                await session.execute(
                    delete(InstrumentModel).where(InstrumentModel.id == instrument_id)
                )
        application.dependency_overrides.clear()
        await engine.dispose()
