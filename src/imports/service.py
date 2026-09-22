from __future__ import annotations

import asyncio
import hashlib
from collections import Counter
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation, localcontext
from pathlib import PurePosixPath
from typing import BinaryIO
from uuid import UUID

from fastapi import status
from sqlalchemy import Select, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from accounts.models import AccountType
from accounts.service import get_account
from imports.adapters import AdapterDescriptor, AdapterRegistry
from imports.models import (
    ImportBatchFileModel,
    ImportBatchModel,
    ImportCompleteness,
    ImportFileFormat,
    ImportJobModel,
    ImportJobType,
    ImportReconciliationStatus,
    ImportRowModel,
    ImportRowStatus,
    ImportStatus,
)
from imports.schemas import ImportPreviewCurrencySummary, ImportPreviewSummary
from imports.storage import (
    EmptyFileError,
    FileTooLargeError,
    ObjectStorage,
    StorageError,
    StoredObject,
)
from imports.xml_security import XmlSecurityError, XmlSecurityLimits, validate_xml_document
from shared.config import get_settings
from shared.errors import ApiErrorException

_FORMAT_SUFFIXES = {
    ImportFileFormat.CSV: ".csv",
    ImportFileFormat.XLSX: ".xlsx",
    ImportFileFormat.XML: ".xml",
    ImportFileFormat.PDF: ".pdf",
}
_CONTENT_TYPES = {
    ImportFileFormat.CSV: frozenset(
        {"application/csv", "application/octet-stream", "text/csv", "text/plain"}
    ),
    ImportFileFormat.XLSX: frozenset(
        {
            "application/octet-stream",
            "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        }
    ),
    ImportFileFormat.XML: frozenset(
        {
            "application/octet-stream",
            "application/xml",
            "text/xml",
        }
    ),
    ImportFileFormat.PDF: frozenset({"application/octet-stream", "application/pdf"}),
}
_MAX_IMPORT_FILES = 8
_PROVIDER_ACCOUNT_TYPES = {
    "tbank_broker_xlsx": AccountType.BROKER,
    "alfa_broker_xml_import": AccountType.BROKER,
    "universal_broker": AccountType.BROKER,
    "bybit_spot_csv_bundle": AccountType.CEX,
}


@dataclass(frozen=True, slots=True)
class ImportUploadPart:
    filename: str | None
    content_type: str | None
    source: BinaryIO


@dataclass(frozen=True, slots=True)
class PortfolioImportQualitySummary:
    period_limited_batch_count: int
    unknown_completeness_batch_count: int
    reconciliation_mismatch_batch_count: int
    unresolved_review_row_count: int


def safe_original_filename(filename: str | None) -> str:
    if filename is None or "\x00" in filename:
        raise ApiErrorException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            code="import_filename_invalid",
            message="Uploaded file must have a valid filename",
        )
    normalized = filename.replace("\\", "/")
    basename = PurePosixPath(normalized).name.strip()
    if not basename or basename in {".", ".."} or len(basename) > 255:
        raise ApiErrorException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            code="import_filename_invalid",
            message="Uploaded file must have a valid filename",
        )
    return basename


def validate_upload_metadata(
    *,
    filename: str,
    content_type: str | None,
    declared_format: ImportFileFormat,
) -> str:
    suffix = PurePosixPath(filename).suffix.lower()
    if suffix != _FORMAT_SUFFIXES[declared_format]:
        raise ApiErrorException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            code="import_extension_mismatch",
            message="Filename extension does not match declared format",
        )
    normalized_content_type = (content_type or "application/octet-stream").split(";", 1)[0]
    if normalized_content_type.strip().lower() not in _CONTENT_TYPES[declared_format]:
        raise ApiErrorException(
            status_code=status.HTTP_415_UNSUPPORTED_MEDIA_TYPE,
            code="import_content_type_invalid",
            message="Uploaded file content type is not allowed for the declared format",
        )
    return suffix


def validate_file_signature(source: BinaryIO, declared_format: ImportFileFormat) -> None:
    source.seek(0)
    prefix = source.read(4096)
    source.seek(0)
    valid = True
    if declared_format == ImportFileFormat.PDF:
        valid = prefix.startswith(b"%PDF-")
    elif declared_format == ImportFileFormat.XLSX:
        valid = prefix.startswith(b"PK\x03\x04")
    elif declared_format == ImportFileFormat.CSV:
        valid = b"\x00" not in prefix
    elif declared_format == ImportFileFormat.XML:
        settings = get_settings()
        try:
            validate_xml_document(
                source,
                limits=XmlSecurityLimits(
                    max_size_bytes=settings.import_max_file_size_bytes,
                    max_depth=settings.import_xml_max_depth,
                    max_elements=settings.import_xml_max_elements,
                    max_value_length=settings.import_xml_max_value_length,
                ),
            )
        except XmlSecurityError as exc:
            error_status = (
                status.HTTP_413_CONTENT_TOO_LARGE
                if exc.code == "import_file_too_large"
                else status.HTTP_422_UNPROCESSABLE_CONTENT
            )
            raise ApiErrorException(
                status_code=error_status,
                code=exc.code,
                message=exc.message,
            ) from exc
    if not valid:
        raise ApiErrorException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            code="import_file_signature_invalid",
            message="File content does not match the declared format",
        )


async def create_import_batch(
    session: AsyncSession,
    storage: ObjectStorage,
    *,
    portfolio_id: UUID,
    account_id: UUID,
    source_provider: str,
    declared_format: ImportFileFormat,
    filename: str | None,
    content_type: str | None,
    source: BinaryIO,
) -> tuple[ImportBatchModel, bool]:
    return await create_import_batch_documents(
        session,
        storage,
        portfolio_id=portfolio_id,
        account_id=account_id,
        source_provider=source_provider,
        declared_format=declared_format,
        uploads=(
            ImportUploadPart(
                filename=filename,
                content_type=content_type,
                source=source,
            ),
        ),
    )


async def create_import_batch_documents(
    session: AsyncSession,
    storage: ObjectStorage,
    *,
    portfolio_id: UUID,
    account_id: UUID,
    source_provider: str,
    declared_format: ImportFileFormat,
    uploads: tuple[ImportUploadPart, ...],
) -> tuple[ImportBatchModel, bool]:
    account = await get_account(session, account_id)
    if account is None or account.portfolio_id != portfolio_id:
        raise ApiErrorException(
            status_code=status.HTTP_404_NOT_FOUND,
            code="account_not_found",
            message="Account was not found",
        )
    expected_account_type = _PROVIDER_ACCOUNT_TYPES.get(source_provider)
    if expected_account_type is not None and account.account_type is not expected_account_type:
        raise ApiErrorException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            code="import_account_type_mismatch",
            message=(
                "Selected account type is incompatible with the chosen import source"
            ),
        )

    if not uploads or len(uploads) > _MAX_IMPORT_FILES:
        raise ApiErrorException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            code="import_file_count_invalid",
            message=f"Import must contain between 1 and {_MAX_IMPORT_FILES} files",
        )

    prepared: list[tuple[str, str, StoredObject]] = []
    try:
        for upload in uploads:
            original_filename = safe_original_filename(upload.filename)
            suffix = validate_upload_metadata(
                filename=original_filename,
                content_type=upload.content_type,
                declared_format=declared_format,
            )
            validate_file_signature(upload.source, declared_format)
            stored = await _store_file(storage, upload.source, suffix=suffix)
            prepared.append((original_filename, suffix, stored))
    except BaseException:
        await _delete_stored_objects(storage, prepared)
        raise

    aggregate_sha256 = _batch_sha256(tuple(item[2].sha256 for item in prepared))
    committed = False

    try:
        duplicate = await get_duplicate_import(session, account_id, aggregate_sha256)
        if duplicate is not None:
            await _delete_stored_objects(storage, prepared)
            return duplicate, True

        primary_filename, _, primary_stored = prepared[0]
        batch = ImportBatchModel(
            portfolio_id=portfolio_id,
            account_id=account_id,
            source_provider=source_provider,
            declared_format=declared_format,
            original_filename=primary_filename,
            storage_key=primary_stored.key,
            file_size_bytes=sum(item[2].size_bytes for item in prepared),
            sha256=aggregate_sha256,
        )
        batch.files.extend(
            ImportBatchFileModel(
                sequence_number=index,
                declared_format=declared_format,
                original_filename=item[0],
                storage_key=item[2].key,
                file_size_bytes=item[2].size_bytes,
                sha256=item[2].sha256,
            )
            for index, item in enumerate(prepared, start=1)
        )
        batch.jobs.append(ImportJobModel(job_type=ImportJobType.PARSE_IMPORT))
        session.add(batch)
        try:
            await session.commit()
        except IntegrityError as exc:
            await session.rollback()
            duplicate = await get_duplicate_import(session, account_id, aggregate_sha256)
            if duplicate is None:
                raise ApiErrorException(
                    status_code=status.HTTP_409_CONFLICT,
                    code="import_conflict",
                    message="Import conflicts with existing data",
                ) from exc
            await _delete_stored_objects(storage, prepared)
            return duplicate, True
        committed = True
        await session.refresh(batch, attribute_names=["files", "jobs"])
        return batch, False
    except BaseException:
        if not committed:
            await _delete_stored_objects(storage, prepared)
        raise


def _batch_sha256(hashes: tuple[str, ...]) -> str:
    if len(hashes) == 1:
        return hashes[0]
    digest = hashlib.sha256()
    digest.update(b"finance-aggregator-import-bundle-v1\0")
    for value in sorted(hashes):
        digest.update(value.encode("ascii"))
        digest.update(b"\0")
    return digest.hexdigest()


async def _delete_stored_objects(
    storage: ObjectStorage,
    prepared: list[tuple[str, str, StoredObject]],
) -> None:
    for _, _, stored in prepared:
        await asyncio.to_thread(storage.delete, stored.key)


async def _store_file(
    storage: ObjectStorage,
    source: BinaryIO,
    *,
    suffix: str,
) -> StoredObject:
    try:
        return await asyncio.to_thread(storage.save, source, suffix=suffix)
    except FileTooLargeError as exc:
        raise ApiErrorException(
            status_code=status.HTTP_413_CONTENT_TOO_LARGE,
            code="import_file_too_large",
            message="Uploaded file exceeds the configured size limit",
        ) from exc
    except EmptyFileError as exc:
        raise ApiErrorException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            code="import_file_empty",
            message="Uploaded file must not be empty",
        ) from exc
    except StorageError as exc:
        raise ApiErrorException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            code="import_file_invalid",
            message="Uploaded file could not be stored",
        ) from exc
    except OSError as exc:
        raise ApiErrorException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            code="import_storage_unavailable",
            message="Import storage is unavailable",
        ) from exc


async def get_duplicate_import(
    session: AsyncSession,
    account_id: UUID,
    sha256: str,
) -> ImportBatchModel | None:
    statement: Select[tuple[ImportBatchModel]] = select(ImportBatchModel).where(
        ImportBatchModel.account_id == account_id,
        ImportBatchModel.sha256 == sha256,
    )
    statement = statement.options(selectinload(ImportBatchModel.files))
    result = await session.scalars(statement)
    return result.first()


async def get_import_batch(
    session: AsyncSession,
    batch_id: UUID,
) -> ImportBatchModel | None:
    statement: Select[tuple[ImportBatchModel]] = (
        select(ImportBatchModel)
        .where(ImportBatchModel.id == batch_id)
        .options(
            selectinload(ImportBatchModel.files),
            selectinload(ImportBatchModel.jobs),
        )
    )
    result = await session.scalars(statement)
    return result.first()


async def list_import_batches(
    session: AsyncSession,
    *,
    portfolio_id: UUID | None,
    account_id: UUID | None,
    import_status: ImportStatus | None,
    limit: int,
    offset: int,
) -> list[ImportBatchModel]:
    statement: Select[tuple[ImportBatchModel]] = select(ImportBatchModel).options(
        selectinload(ImportBatchModel.files)
    )
    if portfolio_id is not None:
        statement = statement.where(ImportBatchModel.portfolio_id == portfolio_id)
    if account_id is not None:
        statement = statement.where(ImportBatchModel.account_id == account_id)
    if import_status is not None:
        statement = statement.where(ImportBatchModel.status == import_status)
    statement = statement.order_by(
        ImportBatchModel.created_at.desc(), ImportBatchModel.id.desc()
    ).limit(limit).offset(offset)
    return list(await session.scalars(statement))


async def get_portfolio_import_quality_summary(
    session: AsyncSession,
    *,
    portfolio_id: UUID,
) -> PortfolioImportQualitySummary:
    batches = list(
        await session.scalars(
            select(ImportBatchModel).where(
                ImportBatchModel.portfolio_id == portfolio_id,
                ImportBatchModel.status.not_in(
                    (ImportStatus.FAILED, ImportStatus.ROLLED_BACK)
                ),
            )
        )
    )
    committed_statuses = {
        ImportStatus.COMMITTED,
        ImportStatus.RECALCULATING,
        ImportStatus.COMPLETED,
    }
    committed = [item for item in batches if item.status in committed_statuses]
    return PortfolioImportQualitySummary(
        period_limited_batch_count=sum(
            item.completeness
            in {
                ImportCompleteness.PERIOD_LEDGER,
                ImportCompleteness.SNAPSHOT_WITH_MOVEMENTS,
            }
            for item in committed
        ),
        unknown_completeness_batch_count=sum(
            item.completeness is ImportCompleteness.UNKNOWN for item in committed
        ),
        reconciliation_mismatch_batch_count=sum(
            item.reconciliation_status is ImportReconciliationStatus.MISMATCH
            for item in committed
        ),
        unresolved_review_row_count=sum(
            item.warning_rows + item.error_rows + item.duplicate_rows
            for item in batches
            if item.status is ImportStatus.AWAITING_REVIEW
        ),
    )


async def list_import_rows(
    session: AsyncSession,
    batch_id: UUID,
    *,
    limit: int,
    offset: int,
) -> list[ImportRowModel]:
    statement: Select[tuple[ImportRowModel]] = (
        select(ImportRowModel)
        .where(ImportRowModel.batch_id == batch_id)
        .options(selectinload(ImportRowModel.resolutions))
        .order_by(ImportRowModel.sequence_number, ImportRowModel.id)
        .limit(limit)
        .offset(offset)
    )
    return list(await session.scalars(statement))


def list_import_formats(registry: AdapterRegistry) -> tuple[AdapterDescriptor, ...]:
    return registry.descriptors()


@dataclass(slots=True)
class _CurrencyAccumulator:
    trade_buys: int = 0
    trade_sells: int = 0
    income: Decimal = Decimal(0)
    fees: Decimal = Decimal(0)
    taxes: Decimal = Decimal(0)
    cash_in: Decimal = Decimal(0)
    cash_out: Decimal = Decimal(0)


async def get_import_preview_summary(
    session: AsyncSession,
    batch_id: UUID,
) -> ImportPreviewSummary:
    result = await session.execute(
        select(ImportRowModel.normalized_candidate).where(
            ImportRowModel.batch_id == batch_id,
            ImportRowModel.status.in_(
                [
                    ImportRowStatus.READY,
                    ImportRowStatus.WARNING,
                    ImportRowStatus.COMMITTED,
                ]
            ),
            ImportRowModel.normalized_candidate.is_not(None),
        )
    )
    operation_counts: Counter[str] = Counter()
    diagnostic_counts: Counter[str] = Counter()
    currency_totals: dict[str, _CurrencyAccumulator] = {}
    with localcontext() as context:
        context.prec = 80
        for (candidate,) in result.all():
            if not isinstance(candidate, dict):
                continue
            operation_type = candidate.get("operation_type")
            payload = candidate.get("payload")
            if not isinstance(operation_type, str) or not isinstance(payload, dict):
                continue
            operation_counts[operation_type] += 1
            _add_candidate_to_summary(operation_type, payload, currency_totals)
    diagnostics_result = await session.execute(
        select(ImportRowModel.warnings, ImportRowModel.errors).where(
            ImportRowModel.batch_id == batch_id
        )
    )
    for warnings, errors in diagnostics_result.all():
        for diagnostic in [*warnings, *errors]:
            code = diagnostic.get("code") if isinstance(diagnostic, dict) else None
            if isinstance(code, str):
                diagnostic_counts[code] += 1
    return ImportPreviewSummary(
        operation_counts=dict(sorted(operation_counts.items())),
        currency_totals={
            currency: ImportPreviewCurrencySummary(
                trade_buys=totals.trade_buys,
                trade_sells=totals.trade_sells,
                income=format(totals.income, "f"),
                fees=format(totals.fees, "f"),
                taxes=format(totals.taxes, "f"),
                cash_in=format(totals.cash_in, "f"),
                cash_out=format(totals.cash_out, "f"),
            )
            for currency, totals in sorted(currency_totals.items())
        },
        diagnostic_counts=dict(sorted(diagnostic_counts.items())),
    )


def _add_candidate_to_summary(
    operation_type: str,
    payload: dict[str, object],
    totals_by_currency: dict[str, _CurrencyAccumulator],
) -> None:
    currency_field: str | None = None
    amount_field: str | None = None
    accumulator_field: str | None = None
    if operation_type == "trade":
        currency_field = "price_currency"
    elif operation_type == "income":
        currency_field, amount_field, accumulator_field = "currency", "amount", "income"
    elif operation_type == "fee":
        currency_field, amount_field, accumulator_field = "currency", "amount", "fees"
    elif operation_type == "tax":
        currency_field, amount_field, accumulator_field = "currency", "amount", "taxes"
    elif operation_type == "cash_movement":
        currency_field, amount_field = "currency", "amount"
        accumulator_field = "cash_in" if payload.get("direction") == "deposit" else "cash_out"
    if currency_field is None:
        return
    currency = payload.get(currency_field)
    if not isinstance(currency, str):
        return
    totals = totals_by_currency.setdefault(currency, _CurrencyAccumulator())
    if operation_type == "trade":
        if payload.get("side") == "buy":
            totals.trade_buys += 1
        elif payload.get("side") == "sell":
            totals.trade_sells += 1
        return
    amount = payload.get(amount_field) if amount_field is not None else None
    if not isinstance(amount, str) or accumulator_field is None:
        return
    try:
        setattr(totals, accumulator_field, getattr(totals, accumulator_field) + Decimal(amount))
    except InvalidOperation:
        return
