import os
from collections.abc import AsyncIterator
from pathlib import Path
from uuid import UUID, uuid4

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import delete
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from apps.api.main import create_app
from imports.adapters import (
    AdapterRegistry,
    DetectionResult,
    ImportDocument,
    ParsedImport,
    ParsedRow,
    ValidationResult,
)
from imports.jobs import claim_next_job, finish_job
from imports.models import ImportBatchModel, ImportFileFormat
from imports.processor import process_import_batch
from imports.storage import LocalObjectStorage, get_object_storage
from portfolios.models import PortfolioModel
from shared.database import get_db_session


class _WorkerTestAdapter:
    format_id = "universal_broker"
    version = "1.0"
    supported_file_formats = frozenset({ImportFileFormat.CSV})

    def detect(self, document: ImportDocument) -> DetectionResult:
        return DetectionResult(matched=document.stream.read(4) == b"Date")

    def parse(self, document: ImportDocument) -> ParsedImport:
        del document
        return ParsedImport(
            rows=(
                ParsedRow(
                    sequence_number=1,
                    source_row_number=2,
                    raw_data={"Date": "2026-08-03", "Type": "Buy"},
                    normalized_candidate={"operation_type": "trade"},
                    warnings=({"code": "instrument_match_required"},),
                ),
            )
        )

    def validate(self, parsed: ParsedImport) -> ValidationResult:
        return ValidationResult(rows=parsed.rows)


@pytest.mark.postgres
@pytest.mark.asyncio
async def test_import_upload_deduplication_preview_and_job_queue(tmp_path: Path) -> None:
    database_url = os.getenv("FINANCE_TEST_DATABASE_URL")
    if database_url is None:
        pytest.skip("FINANCE_TEST_DATABASE_URL is not configured")

    engine = create_async_engine(database_url)
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    storage = LocalObjectStorage(tmp_path / "imports", max_file_size_bytes=1024)

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
    batch_id: UUID | None = None
    storage_key: str | None = None

    try:
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            portfolio_response = await client.post(
                "/api/v1/portfolios",
                json={"name": f"Import {suffix}", "base_currency": "USD"},
            )
            assert portfolio_response.status_code == 201
            portfolio_id = UUID(portfolio_response.json()["id"])
            account_response = await client.post(
                f"/api/v1/portfolios/{portfolio_id}/accounts",
                json={"name": "Synthetic broker", "account_type": "broker"},
            )
            assert account_response.status_code == 201
            account_id = account_response.json()["id"]
            form = {
                "portfolio_id": str(portfolio_id),
                "account_id": account_id,
                "source_provider": "universal_broker",
                "declared_format": "csv",
            }
            content = b"Date,Type,ExternalId\n2026-08-03,Buy,SYN-1\n"

            upload_response = await client.post(
                "/api/v1/imports",
                data=form,
                files={"file": ("../../private.csv", content, "text/csv")},
            )
            assert upload_response.status_code == 201, upload_response.text
            upload = upload_response.json()
            batch_id = UUID(upload["batch"]["id"])
            assert upload["duplicate"] is False
            assert upload["batch"]["original_filename"] == "private.csv"
            assert "storage_key" not in upload["batch"]
            assert upload["batch"]["status"] == "uploaded"

            status_response = await client.get(f"/api/v1/imports/{batch_id}")
            assert status_response.status_code == 200
            assert status_response.json()["jobs"][0]["status"] == "pending"

            preview_response = await client.get(f"/api/v1/imports/{batch_id}/preview")
            assert preview_response.status_code == 200
            assert preview_response.json()["items"] == []
            assert preview_response.json()["total_rows"] == 0

            duplicate_response = await client.post(
                "/api/v1/imports",
                data=form,
                files={"file": ("renamed.csv", content, "text/csv")},
            )
            assert duplicate_response.status_code == 200
            assert duplicate_response.json()["duplicate"] is True
            assert duplicate_response.json()["batch"]["id"] == str(batch_id)

            invalid_response = await client.post(
                "/api/v1/imports",
                data={**form, "declared_format": "pdf"},
                files={"file": ("invalid.pdf", b"not a pdf", "application/pdf")},
            )
            assert invalid_response.status_code == 422
            assert invalid_response.json()["error"]["code"] == "import_file_signature_invalid"

        async with sessions() as session:
            stored_batch = await session.get(ImportBatchModel, batch_id)
            assert stored_batch is not None
            storage_key = stored_batch.storage_key

        async with sessions() as session:
            job = await claim_next_job(session, worker_id="worker-one")
        assert job is not None
        assert job.batch_id == batch_id
        async with sessions() as session:
            assert await claim_next_job(session, worker_id="worker-two") is None

        registry = AdapterRegistry()
        registry.register(_WorkerTestAdapter())
        await process_import_batch(
            sessions,
            storage,
            registry,
            batch_id=batch_id,
        )
        async with sessions() as session:
            await finish_job(
                session,
                job_id=job.id,
                worker_id="worker-one",
                succeeded=True,
            )

        async with AsyncClient(transport=transport, base_url="http://test") as client:
            processed_status = await client.get(f"/api/v1/imports/{batch_id}")
            assert processed_status.status_code == 200
            assert processed_status.json()["batch"]["status"] == "awaiting_review"
            assert processed_status.json()["jobs"][0]["status"] == "succeeded"
            processed_preview = await client.get(f"/api/v1/imports/{batch_id}/preview")
            assert processed_preview.status_code == 200
            assert processed_preview.json()["warning_rows"] == 1
            assert processed_preview.json()["items"][0]["raw_data"] == {
                "Date": "2026-08-03",
                "Type": "Buy",
            }
            assert processed_preview.json()["items"][0]["normalized_candidate"] == {
                "operation_type": "trade"
            }
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
        if storage_key is not None:
            storage.delete(storage_key)
        application.dependency_overrides.clear()
        await engine.dispose()
