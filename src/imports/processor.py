from __future__ import annotations

import asyncio
import hashlib
from collections import Counter
from uuid import UUID

from sqlalchemy import delete
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from imports.adapters import (
    AdapterRegistry,
    DetectionResult,
    ImportAdapter,
    ImportDocument,
    ParsedRow,
    ValidationResult,
)
from imports.models import (
    ImportBatchModel,
    ImportRowModel,
    ImportRowStatus,
    ImportStatus,
)
from imports.storage import ObjectStorage


class ImportProcessingError(Exception):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


async def process_import_batch(
    sessions: async_sessionmaker[AsyncSession],
    storage: ObjectStorage,
    registry: AdapterRegistry,
    *,
    batch_id: UUID,
) -> None:
    async with sessions.begin() as session:
        batch = await session.get(ImportBatchModel, batch_id, with_for_update=True)
        if batch is None:
            raise ImportProcessingError("import_batch_not_found", "Import batch was not found")
        if batch.status not in {ImportStatus.UPLOADED, ImportStatus.FAILED}:
            raise ImportProcessingError(
                "import_status_invalid",
                "Import batch cannot be parsed from its current status",
            )
        batch.status = ImportStatus.DETECTING
        batch.error_summary = None
        storage_key = batch.storage_key
        original_filename = batch.original_filename
        declared_format = batch.declared_format
        size_bytes = batch.file_size_bytes
        sha256 = batch.sha256
        source_provider = batch.source_provider

    try:
        stream = await asyncio.to_thread(storage.open, storage_key)
    except OSError as exc:
        raise ImportProcessingError(
            "import_file_unavailable",
            "Stored import file is unavailable",
        ) from exc

    try:
        document = ImportDocument(
            original_filename=original_filename,
            declared_format=declared_format,
            size_bytes=size_bytes,
            sha256=sha256,
            stream=stream,
        )
        await asyncio.to_thread(_verify_document, document)
        adapter, detection = await asyncio.to_thread(
            _detect_adapter,
            registry,
            document,
            source_provider,
        )
        async with sessions.begin() as session:
            batch = await session.get(ImportBatchModel, batch_id, with_for_update=True)
            if batch is None:
                raise ImportProcessingError(
                    "import_batch_not_found",
                    "Import batch was not found",
                )
            batch.detected_format = adapter.format_id
            batch.detected_version = adapter.version
            batch.completeness = detection.completeness
            batch.status = ImportStatus.PARSING
            batch.error_summary = _diagnostic_summary(detection.diagnostics)

        validated = await asyncio.to_thread(_parse_and_validate, adapter, document)
    finally:
        await asyncio.to_thread(stream.close)

    prepared_rows = [_prepare_row(batch_id, row) for row in validated.rows]
    counts = Counter(row.status for row in prepared_rows)
    async with sessions.begin() as session:
        batch = await session.get(ImportBatchModel, batch_id, with_for_update=True)
        if batch is None:
            raise ImportProcessingError("import_batch_not_found", "Import batch was not found")
        await session.execute(delete(ImportRowModel).where(ImportRowModel.batch_id == batch_id))
        session.add_all(prepared_rows)
        batch.total_rows = len(prepared_rows)
        batch.ready_rows = counts[ImportRowStatus.READY]
        batch.warning_rows = counts[ImportRowStatus.WARNING]
        batch.error_rows = counts[ImportRowStatus.ERROR]
        batch.duplicate_rows = counts[ImportRowStatus.DUPLICATE]
        batch.excluded_rows = counts[ImportRowStatus.EXCLUDED]
        batch.error_summary = _diagnostic_summary(
            detection.diagnostics + validated.diagnostics
        )
        if batch.error_rows or batch.warning_rows or batch.duplicate_rows:
            batch.status = ImportStatus.AWAITING_REVIEW
        else:
            batch.status = ImportStatus.READY_TO_COMMIT


async def fail_import_batch(
    sessions: async_sessionmaker[AsyncSession],
    *,
    batch_id: UUID,
    code: str,
    message: str,
) -> None:
    async with sessions.begin() as session:
        batch = await session.get(ImportBatchModel, batch_id, with_for_update=True)
        if batch is None:
            return
        batch.status = ImportStatus.FAILED
        batch.error_summary = {"code": code, "message": message}


def _detect_adapter(
    registry: AdapterRegistry,
    document: ImportDocument,
    source_provider: str,
) -> tuple[ImportAdapter, DetectionResult]:
    for adapter in registry.candidates(document.declared_format):
        if adapter.format_id != source_provider:
            continue
        document.stream.seek(0)
        detection = adapter.detect(document)
        if detection.matched:
            return adapter, detection
    raise ImportProcessingError(
        "import_adapter_not_found",
        "No registered adapter matched the uploaded file",
    )


def _parse_and_validate(
    adapter: ImportAdapter,
    document: ImportDocument,
) -> ValidationResult:
    document.stream.seek(0)
    parsed = adapter.parse(document)
    return adapter.validate(parsed)


def _verify_document(document: ImportDocument) -> None:
    document.stream.seek(0)
    digest = hashlib.sha256()
    size_bytes = 0
    while chunk := document.stream.read(1024 * 1024):
        size_bytes += len(chunk)
        if size_bytes > document.size_bytes:
            raise ImportProcessingError(
                "import_file_integrity_error",
                "Stored import file failed integrity verification",
            )
        digest.update(chunk)
    document.stream.seek(0)
    if size_bytes != document.size_bytes or digest.hexdigest() != document.sha256:
        raise ImportProcessingError(
            "import_file_integrity_error",
            "Stored import file failed integrity verification",
        )


def _prepare_row(batch_id: UUID, row: ParsedRow) -> ImportRowModel:
    errors = list(row.errors)
    warnings = list(row.warnings)
    status = row.status
    if status is None:
        if errors:
            status = ImportRowStatus.ERROR
        elif warnings:
            status = ImportRowStatus.WARNING
        elif row.normalized_candidate is None:
            status = ImportRowStatus.ERROR
            errors.append(
                {
                    "code": "normalized_candidate_missing",
                    "message": "Adapter did not produce an operation candidate",
                }
            )
        else:
            status = ImportRowStatus.READY
    return ImportRowModel(
        batch_id=batch_id,
        sequence_number=row.sequence_number,
        source_page=row.source_page,
        source_sheet=row.source_sheet,
        source_row_number=row.source_row_number,
        raw_data=row.raw_data,
        normalized_candidate=row.normalized_candidate,
        status=status,
        warnings=warnings,
        errors=errors,
    )


def _diagnostic_summary(
    diagnostics: tuple[dict[str, object], ...],
) -> dict[str, object] | None:
    if not diagnostics:
        return None
    return {"diagnostics": list(diagnostics)}
