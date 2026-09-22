from __future__ import annotations

from datetime import date
from typing import Annotated, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, StringConstraints

from imports.models import (
    ImportCompleteness,
    ImportFileFormat,
    ImportJobStatus,
    ImportJobType,
    ImportReconciliationStatus,
    ImportResolutionSource,
    ImportResolutionType,
    ImportRowStatus,
    ImportStatus,
)
from shared.exact import AwareDateTime

type ExactSummaryValue = Annotated[
    str,
    StringConstraints(strict=True, pattern=r"^(?:0|[1-9][0-9]*)(?:\.[0-9]+)?$"),
]

type SourceProvider = Annotated[
    str,
    StringConstraints(
        strict=True,
        strip_whitespace=True,
        min_length=1,
        max_length=100,
        pattern=r"^[a-z][a-z0-9]*(?:[._-][a-z0-9]+)*$",
    ),
]
type ResolutionNote = Annotated[
    str,
    StringConstraints(strict=True, strip_whitespace=True, min_length=1, max_length=1000),
]


class ImportResolutionResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True, extra="forbid")

    id: UUID
    resolution_type: ImportResolutionType
    source: ImportResolutionSource
    payload: dict[str, object]
    note: str | None
    created_at: AwareDateTime
    updated_at: AwareDateTime


class ImportRowResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True, extra="forbid")

    id: UUID
    sequence_number: int
    source_file_id: UUID | None
    source_page: int | None
    source_sheet: str | None
    source_row_number: int | None
    raw_data: dict[str, object]
    normalized_candidate: dict[str, object] | None
    reconciliation_data: dict[str, object] | None
    status: ImportRowStatus
    warnings: list[dict[str, object]]
    errors: list[dict[str, object]]
    fingerprint: str | None
    matched_operation_id: UUID | None
    resolutions: list[ImportResolutionResponse]
    created_at: AwareDateTime
    updated_at: AwareDateTime


class ImportBatchFileResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True, extra="forbid")

    id: UUID
    sequence_number: int
    declared_format: ImportFileFormat
    original_filename: str
    file_size_bytes: int
    sha256: str
    detected_document_type: str | None
    created_at: AwareDateTime
    updated_at: AwareDateTime


class ImportBatchResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True, extra="forbid")

    id: UUID
    portfolio_id: UUID
    account_id: UUID
    source_provider: str
    declared_format: ImportFileFormat
    detected_format: str | None
    detected_version: str | None
    original_filename: str
    file_size_bytes: int
    sha256: str
    files: list[ImportBatchFileResponse]
    reporting_period_start: date | None
    reporting_period_end: date | None
    completeness: ImportCompleteness
    status: ImportStatus
    total_rows: int
    ready_rows: int
    warning_rows: int
    error_rows: int
    duplicate_rows: int
    excluded_rows: int
    error_summary: dict[str, object] | None
    reconciliation_status: ImportReconciliationStatus
    reconciliation_summary: dict[str, object] | None
    created_at: AwareDateTime
    updated_at: AwareDateTime


class ImportBatchListResponse(BaseModel):
    items: list[ImportBatchResponse]
    limit: int
    offset: int


class ImportJobResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True, extra="forbid")

    id: UUID
    job_type: ImportJobType
    status: ImportJobStatus
    attempts: int
    available_at: AwareDateTime
    last_error: dict[str, object] | None
    created_at: AwareDateTime
    updated_at: AwareDateTime


class ImportUploadResponse(BaseModel):
    batch: ImportBatchResponse
    duplicate: bool


class ImportStatusResponse(BaseModel):
    batch: ImportBatchResponse
    jobs: list[ImportJobResponse]


class ImportRowListResponse(BaseModel):
    items: list[ImportRowResponse]
    limit: int
    offset: int


class ImportPreviewCurrencySummary(BaseModel):
    trade_buys: int = 0
    trade_sells: int = 0
    income: ExactSummaryValue = "0"
    fees: ExactSummaryValue = "0"
    taxes: ExactSummaryValue = "0"
    cash_in: ExactSummaryValue = "0"
    cash_out: ExactSummaryValue = "0"


class ImportPreviewSummary(BaseModel):
    operation_counts: dict[str, int]
    currency_totals: dict[str, ImportPreviewCurrencySummary]
    diagnostic_counts: dict[str, int]


class ImportPreviewResponse(BaseModel):
    batch_id: UUID
    source_provider: str
    detected_format: str | None
    detected_version: str | None
    reporting_period_start: date | None
    reporting_period_end: date | None
    completeness: ImportCompleteness
    reconciliation_status: ImportReconciliationStatus
    reconciliation_summary: dict[str, object] | None
    status: ImportStatus
    total_rows: int
    ready_rows: int
    warning_rows: int
    error_rows: int
    duplicate_rows: int
    excluded_rows: int
    summary: ImportPreviewSummary
    items: list[ImportRowResponse]
    limit: int
    offset: int


class ImportFormatResponse(BaseModel):
    format_id: str
    version: str
    supported_file_formats: list[ImportFileFormat] = Field(min_length=1)


class ImportFormatListResponse(BaseModel):
    items: list[ImportFormatResponse]


class ExcludeImportRowRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    action: Literal["exclude"]
    note: ResolutionNote | None = None


class MatchImportInstrumentRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    action: Literal["match_instrument"]
    target: Literal["instrument", "sold_instrument", "bought_instrument"] = "instrument"
    instrument_id: UUID
    note: ResolutionNote | None = None


class AllowImportDuplicateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    action: Literal["allow_duplicate"]
    note: ResolutionNote


type ImportRowResolutionRequest = Annotated[
    ExcludeImportRowRequest | MatchImportInstrumentRequest | AllowImportDuplicateRequest,
    Field(discriminator="action"),
]


class ImportConfirmResponse(BaseModel):
    batch_id: UUID
    status: ImportStatus
    operation_count: int
    operation_ids: list[UUID]
    idempotent: bool


class ImportRollbackResponse(BaseModel):
    batch_id: UUID
    status: ImportStatus
    rolled_back_operations: int
    idempotent: bool
