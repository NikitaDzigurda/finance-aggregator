from __future__ import annotations

import asyncio
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

from accounts.service import get_account
from imports.adapters import AdapterDescriptor, AdapterRegistry
from imports.models import (
    ImportBatchModel,
    ImportFileFormat,
    ImportJobModel,
    ImportJobType,
    ImportRowModel,
    ImportRowStatus,
)
from imports.schemas import ImportPreviewCurrencySummary, ImportPreviewSummary
from imports.storage import (
    EmptyFileError,
    FileTooLargeError,
    ObjectStorage,
    StorageError,
    StoredObject,
)
from shared.errors import ApiErrorException

_FORMAT_SUFFIXES = {
    ImportFileFormat.CSV: ".csv",
    ImportFileFormat.XLSX: ".xlsx",
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
    ImportFileFormat.PDF: frozenset({"application/octet-stream", "application/pdf"}),
}


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
    account = await get_account(session, account_id)
    if account is None or account.portfolio_id != portfolio_id:
        raise ApiErrorException(
            status_code=status.HTTP_404_NOT_FOUND,
            code="account_not_found",
            message="Account was not found",
        )

    original_filename = safe_original_filename(filename)
    suffix = validate_upload_metadata(
        filename=original_filename,
        content_type=content_type,
        declared_format=declared_format,
    )
    validate_file_signature(source, declared_format)
    stored = await _store_file(storage, source, suffix=suffix)
    committed = False

    try:
        duplicate = await get_duplicate_import(session, account_id, stored.sha256)
        if duplicate is not None:
            await asyncio.to_thread(storage.delete, stored.key)
            return duplicate, True

        batch = ImportBatchModel(
            portfolio_id=portfolio_id,
            account_id=account_id,
            source_provider=source_provider,
            declared_format=declared_format,
            original_filename=original_filename,
            storage_key=stored.key,
            file_size_bytes=stored.size_bytes,
            sha256=stored.sha256,
        )
        batch.jobs.append(ImportJobModel(job_type=ImportJobType.PARSE_IMPORT))
        session.add(batch)
        try:
            await session.commit()
        except IntegrityError as exc:
            await session.rollback()
            duplicate = await get_duplicate_import(session, account_id, stored.sha256)
            if duplicate is None:
                raise ApiErrorException(
                    status_code=status.HTTP_409_CONFLICT,
                    code="import_conflict",
                    message="Import conflicts with existing data",
                ) from exc
            await asyncio.to_thread(storage.delete, stored.key)
            return duplicate, True
        committed = True
        await session.refresh(batch)
        return batch, False
    except BaseException:
        if not committed:
            await asyncio.to_thread(storage.delete, stored.key)
        raise


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
    result = await session.scalars(statement)
    return result.first()


async def get_import_batch(
    session: AsyncSession,
    batch_id: UUID,
) -> ImportBatchModel | None:
    statement: Select[tuple[ImportBatchModel]] = (
        select(ImportBatchModel)
        .where(ImportBatchModel.id == batch_id)
        .options(selectinload(ImportBatchModel.jobs))
    )
    result = await session.scalars(statement)
    return result.first()


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
