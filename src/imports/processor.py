from __future__ import annotations

import asyncio
import hashlib
from collections import Counter
from uuid import UUID

from sqlalchemy import delete
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from sqlalchemy.orm import selectinload

from imports.adapters import (
    AdapterRegistry,
    DetectionResult,
    ImportAdapter,
    ImportDocument,
    ParsedRow,
    ValidationResult,
)
from imports.deduplication import apply_import_deduplication
from imports.instrument_matching import resolve_import_instruments
from imports.models import (
    ImportBatchModel,
    ImportFileFormat,
    ImportReconciliationStatus,
    ImportRowModel,
    ImportRowStatus,
    ImportStatus,
)
from imports.reconciliation import ImportReconciliationError, reconcile_import_rows
from imports.storage import ObjectStorage
from imports.xml_security import XmlSecurityError, XmlSecurityLimits, validate_xml_document
from shared.config import get_settings


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
        batch = await session.get(
            ImportBatchModel,
            batch_id,
            with_for_update=True,
            options=(selectinload(ImportBatchModel.files),),
        )
        if batch is None:
            raise ImportProcessingError("import_batch_not_found", "Import batch was not found")
        if batch.status not in {ImportStatus.UPLOADED, ImportStatus.FAILED}:
            raise ImportProcessingError(
                "import_status_invalid",
                "Import batch cannot be parsed from its current status",
            )
        batch.status = ImportStatus.DETECTING
        batch.error_summary = None
        batch.reconciliation_status = ImportReconciliationStatus.NOT_AVAILABLE
        batch.reconciliation_summary = None
        file_records: tuple[
            tuple[UUID | None, str, ImportFileFormat, str, int, str], ...
        ] = tuple(
            (
                item.id,
                item.original_filename,
                item.declared_format,
                item.storage_key,
                item.file_size_bytes,
                item.sha256,
            )
            for item in batch.files
        )
        if not file_records:
            file_records = (
                (
                    None,
                    batch.original_filename,
                    batch.declared_format,
                    batch.storage_key,
                    batch.file_size_bytes,
                    batch.sha256,
                ),
            )
        source_provider = batch.source_provider
        portfolio_id = batch.portfolio_id
        account_id = batch.account_id

    streams = []
    try:
        for record in file_records:
            streams.append(await asyncio.to_thread(storage.open, record[3]))
    except OSError as exc:
        for opened_stream in streams:
            await asyncio.to_thread(opened_stream.close)
        raise ImportProcessingError(
            "import_file_unavailable",
            "Stored import file is unavailable",
        ) from exc

    try:
        documents = tuple(
            ImportDocument(
                original_filename=record[1],
                declared_format=record[2],
                size_bytes=record[4],
                sha256=record[5],
                stream=stream,
                document_index=index,
            )
            for index, (record, stream) in enumerate(zip(file_records, streams, strict=True))
        )
        for item in documents:
            await asyncio.to_thread(_verify_document, item)
        document = ImportDocument(
            original_filename=documents[0].original_filename,
            declared_format=documents[0].declared_format,
            size_bytes=documents[0].size_bytes,
            sha256=documents[0].sha256,
            stream=documents[0].stream,
            document_index=0,
            bundle_documents=documents,
        )
        adapter, detection = await asyncio.to_thread(
            _detect_adapter,
            registry,
            document,
            source_provider,
        )
        async with sessions.begin() as session:
            batch = await session.get(
                ImportBatchModel,
                batch_id,
                with_for_update=True,
                options=(selectinload(ImportBatchModel.files),),
            )
            if batch is None:
                raise ImportProcessingError(
                    "import_batch_not_found",
                    "Import batch was not found",
                )
            batch.detected_format = adapter.format_id
            batch.detected_version = adapter.version
            batch.completeness = detection.completeness
            batch.reporting_period_start = detection.reporting_period_start
            batch.reporting_period_end = detection.reporting_period_end
            batch.status = ImportStatus.PARSING
            batch.error_summary = _diagnostic_summary(detection.diagnostics)
            by_index = {item.document_index: item.document_type for item in detection.documents}
            for index, batch_file in enumerate(batch.files):
                batch_file.detected_document_type = by_index.get(index)

        validated = await asyncio.to_thread(_parse_and_validate, adapter, document)
    finally:
        for stream in streams:
            await asyncio.to_thread(stream.close)

    async with sessions.begin() as session:
        resolved_rows = await resolve_import_instruments(
            session,
            validated.rows,
            portfolio_id=portfolio_id,
            account_id=account_id,
        )
        deduplicated_rows = await apply_import_deduplication(
            session,
            resolved_rows,
            account_id=account_id,
            source_provider=source_provider,
        )

    file_ids = tuple(record[0] for record in file_records)
    prepared_rows = [_prepare_row(batch_id, row, file_ids) for row in deduplicated_rows]
    try:
        reconciliation = reconcile_import_rows(prepared_rows)
    except ImportReconciliationError as exc:
        raise ImportProcessingError(exc.code, exc.message) from exc
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
        batch.reconciliation_status = reconciliation.status
        batch.reconciliation_summary = reconciliation.summary
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
    selected_detection: DetectionResult | None = None
    for adapter in registry.candidates(document.declared_format):
        if adapter.format_id != source_provider:
            continue
        document.stream.seek(0)
        detection = adapter.detect(document)
        if detection.matched:
            return adapter, detection
        selected_detection = detection
    if selected_detection is not None and selected_detection.diagnostics:
        diagnostic = selected_detection.diagnostics[0]
        code = diagnostic.get("code")
        message = diagnostic.get("message")
        if isinstance(code, str) and isinstance(message, str):
            raise ImportProcessingError(code, message)
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
    if document.declared_format == ImportFileFormat.XML:
        settings = get_settings()
        try:
            validate_xml_document(
                document.stream,
                limits=XmlSecurityLimits(
                    max_size_bytes=settings.import_max_file_size_bytes,
                    max_depth=settings.import_xml_max_depth,
                    max_elements=settings.import_xml_max_elements,
                    max_value_length=settings.import_xml_max_value_length,
                ),
            )
        except XmlSecurityError as exc:
            raise ImportProcessingError(exc.code, exc.message) from exc


def _prepare_row(
    batch_id: UUID,
    row: ParsedRow,
    file_ids: tuple[UUID | None, ...],
) -> ImportRowModel:
    if row.source_document_index < 0 or row.source_document_index >= len(file_ids):
        raise ImportProcessingError(
            "import_source_document_invalid",
            "Adapter referenced an invalid source document",
        )
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
        source_file_id=file_ids[row.source_document_index],
        source_page=row.source_page,
        source_sheet=row.source_sheet,
        source_row_number=row.source_row_number,
        raw_data=row.raw_data,
        normalized_candidate=row.normalized_candidate,
        reconciliation_data=row.reconciliation_data,
        status=status,
        warnings=warnings,
        errors=errors,
        fingerprint=row.fingerprint,
    )


def _diagnostic_summary(
    diagnostics: tuple[dict[str, object], ...],
) -> dict[str, object] | None:
    if not diagnostics:
        return None
    return {"diagnostics": list(diagnostics)}
