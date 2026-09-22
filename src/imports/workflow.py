from __future__ import annotations

from collections import Counter
from typing import NoReturn, cast
from uuid import UUID, uuid4

from fastapi import status
from pydantic import TypeAdapter, ValidationError
from sqlalchemy import Select, delete, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from imports.deduplication import duplicate_override_fingerprint, import_fingerprint
from imports.models import (
    ImportBatchModel,
    ImportFileFormat,
    ImportResolutionModel,
    ImportResolutionSource,
    ImportResolutionType,
    ImportRowModel,
    ImportRowStatus,
    ImportStatus,
)
from imports.reconciliation import ImportReconciliationError, reconcile_import_rows
from imports.schemas import (
    AllowImportDuplicateRequest,
    ExcludeImportRowRequest,
    ImportConfirmResponse,
    ImportRollbackResponse,
    ImportRowResolutionRequest,
    MatchImportInstrumentRequest,
)
from instruments.models import InstrumentModel
from operations.models import OperationModel, OperationSourceType
from operations.schemas import OperationCreate
from operations.service import payload_instrument_id, payload_secondary_instrument_id
from shared.errors import ApiErrorException

_OPERATION_ADAPTER: TypeAdapter[OperationCreate] = TypeAdapter(OperationCreate)
_SOURCE_TYPES = {
    ImportFileFormat.CSV: OperationSourceType.CSV_IMPORT,
    ImportFileFormat.XLSX: OperationSourceType.XLSX_IMPORT,
    ImportFileFormat.XML: OperationSourceType.XML_IMPORT,
    ImportFileFormat.PDF: OperationSourceType.PDF_IMPORT,
}
_INSTRUMENT_DIAGNOSTICS = {
    "instrument_match_required",
    "instrument_reference_conflict",
    "instrument_ticker_mismatch",
    "instrument_isin_not_found",
}


async def resolve_import_row(
    session: AsyncSession,
    *,
    batch_id: UUID,
    row_id: UUID,
    request: ImportRowResolutionRequest,
) -> ImportRowModel:
    batch = await _locked_batch(session, batch_id)
    if batch is None:
        _raise_not_found("import_batch")
    if batch.status not in {ImportStatus.AWAITING_REVIEW, ImportStatus.READY_TO_COMMIT}:
        _raise_conflict(
            "import_resolution_status_invalid",
            "Import rows cannot be resolved from the current batch status",
        )
    row = await session.scalar(
        select(ImportRowModel)
        .where(ImportRowModel.id == row_id, ImportRowModel.batch_id == batch_id)
        .options(selectinload(ImportRowModel.resolutions))
        .with_for_update()
    )
    if row is None:
        _raise_not_found("import_row")
    if row.status in {ImportRowStatus.COMMITTED, ImportRowStatus.EXCLUDED}:
        _raise_conflict(
            "import_row_resolution_invalid",
            "Import row cannot be resolved from its current status",
        )

    if isinstance(request, ExcludeImportRowRequest):
        _upsert_resolution(
            row,
            ImportResolutionType.EXCLUDE_ROW,
            payload={},
            note=request.note,
        )
        row.status = ImportRowStatus.EXCLUDED
    elif isinstance(request, MatchImportInstrumentRequest):
        await _resolve_instrument(session, batch, row, request)
    elif isinstance(request, AllowImportDuplicateRequest):
        _allow_duplicate(batch, row, request)
    else:
        raise AssertionError("Unsupported import row resolution")

    await session.flush()
    await _refresh_batch_review_state(session, batch)
    try:
        await session.commit()
    except IntegrityError as exc:
        await session.rollback()
        raise ApiErrorException(
            status_code=status.HTTP_409_CONFLICT,
            code="import_resolution_conflict",
            message="Import row resolution conflicts with existing data",
        ) from exc
    return row


async def confirm_import(
    session: AsyncSession,
    *,
    batch_id: UUID,
) -> ImportConfirmResponse:
    batch = await _locked_batch(session, batch_id)
    if batch is None:
        _raise_not_found("import_batch")
    rows = await _locked_rows(session, batch_id)
    if batch.status in {
        ImportStatus.COMMITTED,
        ImportStatus.RECALCULATING,
        ImportStatus.COMPLETED,
    }:
        existing_operation_ids = _matched_operation_ids(rows)
        return ImportConfirmResponse(
            batch_id=batch.id,
            status=batch.status,
            operation_count=len(existing_operation_ids),
            operation_ids=existing_operation_ids,
            idempotent=True,
        )
    if batch.status is not ImportStatus.READY_TO_COMMIT:
        _raise_conflict(
            "import_not_ready",
            "Import batch must be fully reviewed before confirmation",
        )
    if any(
        row.status not in {ImportRowStatus.READY, ImportRowStatus.EXCLUDED}
        for row in rows
    ):
        _raise_conflict(
            "import_rows_not_ready",
            "Import batch contains unresolved rows",
        )

    _refresh_reconciliation(batch, rows)

    prepared: list[
        tuple[ImportRowModel, OperationCreate, str | None, str, str]
    ] = []
    for row in rows:
        if row.status is ImportRowStatus.EXCLUDED:
            continue
        if row.normalized_candidate is None:
            _raise_conflict(
                "import_candidate_missing",
                "Import row does not contain a normalized operation candidate",
            )
        operation, source_operation_id = _validated_operation(batch, row.normalized_candidate)
        base_fingerprint = import_fingerprint(
            account_id=batch.account_id,
            source_provider=batch.source_provider,
            candidate=row.normalized_candidate,
        )
        if row.fingerprint not in {None, base_fingerprint}:
            _raise_conflict(
                "import_fingerprint_changed",
                "Import row fingerprint does not match its normalized candidate",
            )
        row.fingerprint = base_fingerprint
        deduplication_key = _deduplication_key(batch, row, base_fingerprint)
        prepared.append(
            (row, operation, source_operation_id, base_fingerprint, deduplication_key)
        )

    deduplication_keys = [item[4] for item in prepared]
    if len(deduplication_keys) != len(set(deduplication_keys)):
        _raise_conflict(
            "import_duplicate_unresolved",
            "Import batch contains unresolved duplicate operations",
        )
    if deduplication_keys:
        existing = await session.scalar(
            select(OperationModel.id).where(
                OperationModel.account_id == batch.account_id,
                OperationModel.deduplication_key.in_(deduplication_keys),
            )
        )
        if existing is not None:
            _raise_conflict(
                "import_duplicate_conflict",
                "A matching imported operation already exists",
            )

    batch.status = ImportStatus.COMMITTING
    operation_ids: list[UUID] = []
    for row, payload, source_operation_id, fingerprint, deduplication_key in prepared:
        operation_id = uuid4()
        ledger_operation = OperationModel(
            id=operation_id,
            portfolio_id=batch.portfolio_id,
            account_id=batch.account_id,
            operation_type=payload.operation_type,
            occurred_at=payload.occurred_at,
            time_precision=payload.time_precision,
            source_type=_SOURCE_TYPES[batch.declared_format],
            import_batch_id=batch.id,
            source_operation_id=source_operation_id,
            source_row_number=row.source_row_number,
            fingerprint=fingerprint,
            deduplication_key=deduplication_key,
            payload=payload.payload.model_dump(mode="json"),
            instrument_id=payload_instrument_id(payload),
            secondary_instrument_id=payload_secondary_instrument_id(payload),
            note=payload.note,
        )
        session.add(ledger_operation)
        row.matched_operation_id = operation_id
        row.status = ImportRowStatus.COMMITTED
        operation_ids.append(operation_id)

    batch.status = ImportStatus.COMMITTED
    batch.ready_rows = 0
    batch.warning_rows = 0
    batch.error_rows = 0
    batch.duplicate_rows = 0
    try:
        await session.commit()
    except IntegrityError as exc:
        await session.rollback()
        raise ApiErrorException(
            status_code=status.HTTP_409_CONFLICT,
            code="import_confirm_conflict",
            message="Import confirmation conflicts with existing ledger data",
        ) from exc
    return ImportConfirmResponse(
        batch_id=batch.id,
        status=batch.status,
        operation_count=len(operation_ids),
        operation_ids=operation_ids,
        idempotent=False,
    )


async def rollback_import(
    session: AsyncSession,
    *,
    batch_id: UUID,
) -> ImportRollbackResponse:
    batch = await _locked_batch(session, batch_id)
    if batch is None:
        _raise_not_found("import_batch")
    rows = await _locked_rows(session, batch_id)
    if batch.status is ImportStatus.ROLLED_BACK:
        return ImportRollbackResponse(
            batch_id=batch.id,
            status=batch.status,
            rolled_back_operations=sum(
                row.status is ImportRowStatus.READY for row in rows
            ),
            idempotent=True,
        )
    if batch.status not in {ImportStatus.COMMITTED, ImportStatus.COMPLETED}:
        _raise_conflict(
            "import_rollback_status_invalid",
            "Import batch cannot be rolled back from its current status",
        )

    operation_ids = _matched_operation_ids(rows)
    if operation_ids:
        correction = await session.scalar(
            select(OperationModel.id).where(
                OperationModel.correction_of_operation_id.in_(operation_ids)
            )
        )
        if correction is not None:
            _raise_conflict(
                "import_rollback_has_corrections",
                "Import cannot be rolled back after a correction references its operations",
            )

    for row in rows:
        if row.matched_operation_id is not None:
            row.matched_operation_id = None
            row.status = ImportRowStatus.READY
    await session.flush()
    deleted = await session.scalars(
        delete(OperationModel)
        .where(OperationModel.import_batch_id == batch.id)
        .returning(OperationModel.id)
    )
    deleted_ids = list(deleted)
    batch.status = ImportStatus.ROLLED_BACK
    counts = Counter(row.status for row in rows)
    batch.ready_rows = counts[ImportRowStatus.READY]
    batch.warning_rows = counts[ImportRowStatus.WARNING]
    batch.error_rows = counts[ImportRowStatus.ERROR]
    batch.duplicate_rows = counts[ImportRowStatus.DUPLICATE]
    batch.excluded_rows = counts[ImportRowStatus.EXCLUDED]
    try:
        await session.commit()
    except IntegrityError as exc:
        await session.rollback()
        raise ApiErrorException(
            status_code=status.HTTP_409_CONFLICT,
            code="import_rollback_conflict",
            message="Import rollback conflicts with dependent ledger data",
        ) from exc
    return ImportRollbackResponse(
        batch_id=batch.id,
        status=batch.status,
        rolled_back_operations=len(deleted_ids),
        idempotent=False,
    )


async def _resolve_instrument(
    session: AsyncSession,
    batch: ImportBatchModel,
    row: ImportRowModel,
    request: MatchImportInstrumentRequest,
) -> None:
    if await session.get(InstrumentModel, request.instrument_id) is None:
        _raise_not_found("instrument")
    candidate = row.normalized_candidate
    if candidate is None or not isinstance(candidate.get("payload"), dict):
        _raise_conflict(
            "import_candidate_missing",
            "Import row does not contain a resolvable operation candidate",
        )
    candidate_copy = dict(candidate)
    payload = dict(cast(dict[str, object], candidate_copy["payload"]))
    reference_key = f"{request.target}_reference"
    instrument_key = f"{request.target}_id"
    if reference_key not in payload:
        _raise_conflict(
            "instrument_resolution_not_required",
            "Import row does not contain an unresolved instrument reference",
        )
    payload.pop(reference_key)
    payload[instrument_key] = str(request.instrument_id)
    candidate_copy["payload"] = payload
    unresolved_targets = {
        key.removesuffix("_reference")
        for key in (
            "instrument_reference",
            "sold_instrument_reference",
            "bought_instrument_reference",
        )
        if key in payload
    }
    if not unresolved_targets:
        _validated_operation(batch, candidate_copy)
    row.normalized_candidate = candidate_copy
    row.warnings = [
        item
        for item in row.warnings
        if not (
            item.get("code") in _INSTRUMENT_DIAGNOSTICS
            and item.get("target", "instrument") == request.target
        )
    ]
    row.errors = [
        item
        for item in row.errors
        if not (
            item.get("code") in _INSTRUMENT_DIAGNOSTICS
            and item.get("target", "instrument") == request.target
        )
    ]
    existing_resolution = next(
        (
            item
            for item in row.resolutions
            if item.resolution_type is ImportResolutionType.INSTRUMENT_MATCH
        ),
        None,
    )
    existing_matches = (
        existing_resolution.payload.get("matches")
        if existing_resolution is not None
        else None
    )
    resolved_targets = dict(existing_matches) if isinstance(existing_matches, dict) else {}
    resolved_targets[request.target] = str(request.instrument_id)
    _upsert_resolution(
        row,
        ImportResolutionType.INSTRUMENT_MATCH,
        payload={"matches": resolved_targets},
        note=request.note,
    )
    if unresolved_targets:
        row.status = _review_status(row)
        return
    base_fingerprint = import_fingerprint(
        account_id=batch.account_id,
        source_provider=batch.source_provider,
        candidate=candidate_copy,
    )
    duplicate = await _fingerprint_exists(session, batch, row, base_fingerprint)
    if duplicate and not _has_resolution(row, ImportResolutionType.ALLOW_DUPLICATE):
        row.fingerprint = base_fingerprint
        row.status = ImportRowStatus.DUPLICATE
        _append_warning_once(
            row,
            "import_duplicate",
            "A matching imported operation already exists",
        )
        return
    row.fingerprint = base_fingerprint
    row.warnings = [item for item in row.warnings if item.get("code") != "import_duplicate"]
    row.status = _review_status(row)


def _allow_duplicate(
    batch: ImportBatchModel,
    row: ImportRowModel,
    request: AllowImportDuplicateRequest,
) -> None:
    if row.status is not ImportRowStatus.DUPLICATE or row.fingerprint is None:
        _raise_conflict(
            "import_duplicate_resolution_invalid",
            "Only a detected duplicate can be explicitly allowed",
        )
    base_fingerprint = row.fingerprint
    _upsert_resolution(
        row,
        ImportResolutionType.ALLOW_DUPLICATE,
        payload={"base_fingerprint": base_fingerprint},
        note=request.note,
    )
    row.warnings = [item for item in row.warnings if item.get("code") != "import_duplicate"]
    row.status = _review_status(row)


def _validated_operation(
    batch: ImportBatchModel,
    candidate: dict[str, object],
) -> tuple[OperationCreate, str | None]:
    candidate_copy = dict(candidate)
    source_operation_id = candidate_copy.pop("source_operation_id", None)
    if source_operation_id is not None and not isinstance(source_operation_id, str):
        _raise_conflict(
            "import_external_id_invalid",
            "Import candidate external ID is invalid",
        )
    try:
        operation = _OPERATION_ADAPTER.validate_python(
            {
                **candidate_copy,
                "portfolio_id": batch.portfolio_id,
                "account_id": batch.account_id,
            }
        )
    except ValidationError as exc:
        raise ApiErrorException(
            status_code=status.HTTP_409_CONFLICT,
            code="import_candidate_invalid",
            message="Import row contains an invalid normalized operation candidate",
        ) from exc
    return operation, source_operation_id


def _deduplication_key(
    batch: ImportBatchModel,
    row: ImportRowModel,
    base_fingerprint: str,
) -> str:
    if _has_resolution(row, ImportResolutionType.ALLOW_DUPLICATE):
        return duplicate_override_fingerprint(
            base_fingerprint,
            batch_id=batch.id,
            row_id=row.id,
        )
    return base_fingerprint


async def _fingerprint_exists(
    session: AsyncSession,
    batch: ImportBatchModel,
    row: ImportRowModel,
    fingerprint: str,
) -> bool:
    operation = await session.scalar(
        select(OperationModel.id).where(
            OperationModel.account_id == batch.account_id,
            OperationModel.fingerprint == fingerprint,
        )
    )
    if operation is not None:
        return True
    other_row = await session.scalar(
        select(ImportRowModel.id).where(
            ImportRowModel.batch_id == batch.id,
            ImportRowModel.id != row.id,
            ImportRowModel.fingerprint == fingerprint,
            ImportRowModel.status != ImportRowStatus.EXCLUDED,
        )
    )
    return other_row is not None


async def _locked_batch(
    session: AsyncSession,
    batch_id: UUID,
) -> ImportBatchModel | None:
    statement: Select[tuple[ImportBatchModel]] = (
        select(ImportBatchModel)
        .where(ImportBatchModel.id == batch_id)
        .with_for_update()
    )
    result = await session.scalars(statement)
    return result.one_or_none()


async def _locked_rows(
    session: AsyncSession,
    batch_id: UUID,
) -> list[ImportRowModel]:
    return list(
        await session.scalars(
            select(ImportRowModel)
            .where(ImportRowModel.batch_id == batch_id)
            .options(selectinload(ImportRowModel.resolutions))
            .order_by(ImportRowModel.sequence_number, ImportRowModel.id)
            .with_for_update()
        )
    )


async def _refresh_batch_review_state(
    session: AsyncSession,
    batch: ImportBatchModel,
) -> None:
    rows = list(
        await session.scalars(
            select(ImportRowModel).where(ImportRowModel.batch_id == batch.id)
        )
    )
    counts = Counter(row.status for row in rows)
    batch.total_rows = len(rows)
    batch.ready_rows = counts[ImportRowStatus.READY]
    batch.warning_rows = counts[ImportRowStatus.WARNING]
    batch.error_rows = counts[ImportRowStatus.ERROR]
    batch.duplicate_rows = counts[ImportRowStatus.DUPLICATE]
    batch.excluded_rows = counts[ImportRowStatus.EXCLUDED]
    unresolved = (
        batch.warning_rows
        or batch.error_rows
        or batch.duplicate_rows
        or counts[ImportRowStatus.PENDING]
    )
    batch.status = (
        ImportStatus.AWAITING_REVIEW if unresolved else ImportStatus.READY_TO_COMMIT
    )
    _refresh_reconciliation(batch, rows)


def _refresh_reconciliation(
    batch: ImportBatchModel,
    rows: list[ImportRowModel],
) -> None:
    try:
        result = reconcile_import_rows(rows)
    except ImportReconciliationError as exc:
        _raise_conflict(exc.code, exc.message)
    batch.reconciliation_status = result.status
    batch.reconciliation_summary = result.summary


def _upsert_resolution(
    row: ImportRowModel,
    resolution_type: ImportResolutionType,
    *,
    payload: dict[str, object],
    note: str | None,
) -> None:
    resolution = next(
        (item for item in row.resolutions if item.resolution_type is resolution_type),
        None,
    )
    if resolution is None:
        row.resolutions.append(
            ImportResolutionModel(
                batch_id=row.batch_id,
                row_id=row.id,
                resolution_type=resolution_type,
                source=ImportResolutionSource.USER,
                payload=payload,
                note=note,
            )
        )
        return
    resolution.source = ImportResolutionSource.USER
    resolution.payload = payload
    resolution.note = note


def _has_resolution(row: ImportRowModel, resolution_type: ImportResolutionType) -> bool:
    return any(item.resolution_type is resolution_type for item in row.resolutions)


def _review_status(row: ImportRowModel) -> ImportRowStatus:
    if row.errors:
        return ImportRowStatus.ERROR
    if row.warnings:
        return ImportRowStatus.WARNING
    return ImportRowStatus.READY


def _append_warning_once(row: ImportRowModel, code: str, message: str) -> None:
    if any(item.get("code") == code for item in row.warnings):
        return
    row.warnings = [*row.warnings, {"code": code, "message": message}]


def _matched_operation_ids(rows: list[ImportRowModel]) -> list[UUID]:
    return [
        row.matched_operation_id
        for row in rows
        if row.matched_operation_id is not None
    ]


def _raise_not_found(resource: str) -> NoReturn:
    raise ApiErrorException(
        status_code=status.HTTP_404_NOT_FOUND,
        code=f"{resource}_not_found",
        message=f"{resource.replace('_', ' ').title()} was not found",
    )


def _raise_conflict(code: str, message: str) -> NoReturn:
    raise ApiErrorException(
        status_code=status.HTTP_409_CONFLICT,
        code=code,
        message=message,
    )
