from __future__ import annotations

from datetime import date
from typing import Annotated
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, StringConstraints

from imports.models import (
    ImportCompleteness,
    ImportFileFormat,
    ImportJobStatus,
    ImportJobType,
    ImportResolutionSource,
    ImportResolutionType,
    ImportRowStatus,
    ImportStatus,
)
from shared.exact import AwareDateTime

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
    source_page: int | None
    source_sheet: str | None
    source_row_number: int | None
    raw_data: dict[str, object]
    normalized_candidate: dict[str, object] | None
    status: ImportRowStatus
    warnings: list[dict[str, object]]
    errors: list[dict[str, object]]
    matched_operation_id: UUID | None
    resolutions: list[ImportResolutionResponse]
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
    created_at: AwareDateTime
    updated_at: AwareDateTime


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


class ImportPreviewResponse(BaseModel):
    batch_id: UUID
    status: ImportStatus
    total_rows: int
    ready_rows: int
    warning_rows: int
    error_rows: int
    duplicate_rows: int
    excluded_rows: int
    items: list[ImportRowResponse]
    limit: int
    offset: int


class ImportFormatResponse(BaseModel):
    format_id: str
    version: str
    supported_file_formats: list[ImportFileFormat] = Field(min_length=1)


class ImportFormatListResponse(BaseModel):
    items: list[ImportFormatResponse]
