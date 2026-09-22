from __future__ import annotations

from datetime import date, datetime
from enum import StrEnum
from uuid import UUID, uuid4

from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    Date,
    DateTime,
    Enum,
    ForeignKey,
    ForeignKeyConstraint,
    Index,
    Integer,
    String,
    UniqueConstraint,
    Uuid,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from shared.database import Base
from shared.models import TimestampMixin


class ImportFileFormat(StrEnum):
    CSV = "csv"
    XLSX = "xlsx"
    XML = "xml"
    PDF = "pdf"


class ImportCompleteness(StrEnum):
    FULL_LEDGER = "full_ledger"
    PERIOD_LEDGER = "period_ledger"
    SNAPSHOT_WITH_MOVEMENTS = "snapshot_with_movements"
    UNKNOWN = "unknown"


class ImportReconciliationStatus(StrEnum):
    NOT_AVAILABLE = "not_available"
    MATCHED = "matched"
    MISMATCH = "mismatch"


class ImportStatus(StrEnum):
    UPLOADED = "uploaded"
    DETECTING = "detecting"
    PARSING = "parsing"
    AWAITING_REVIEW = "awaiting_review"
    READY_TO_COMMIT = "ready_to_commit"
    COMMITTING = "committing"
    COMMITTED = "committed"
    RECALCULATING = "recalculating"
    COMPLETED = "completed"
    FAILED = "failed"
    ROLLED_BACK = "rolled_back"


class ImportRowStatus(StrEnum):
    PENDING = "pending"
    READY = "ready"
    WARNING = "warning"
    ERROR = "error"
    DUPLICATE = "duplicate"
    EXCLUDED = "excluded"
    COMMITTED = "committed"


class ImportResolutionType(StrEnum):
    INSTRUMENT_MATCH = "instrument_match"
    CURRENCY_SELECTION = "currency_selection"
    EXCLUDE_ROW = "exclude_row"
    ALLOW_DUPLICATE = "allow_duplicate"
    OPERATION_TYPE_CORRECTION = "operation_type_correction"


class ImportResolutionSource(StrEnum):
    USER = "user"
    ADAPTER = "adapter"


class ImportJobType(StrEnum):
    PARSE_IMPORT = "parse_import"
    CONFIRM_IMPORT = "confirm_import"
    RECALCULATE_PORTFOLIO = "recalculate_portfolio"
    ROLLBACK_IMPORT = "rollback_import"
    FX_SYNC = "fx_sync"
    MARKET_DATA_SYNC = "market_data_sync"


class ImportJobStatus(StrEnum):
    PENDING = "pending"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    CANCELLED = "cancelled"


def _enum_values(enum_class: type[StrEnum]) -> list[str]:
    return [member.value for member in enum_class]


class ImportBatchModel(TimestampMixin, Base):
    __tablename__ = "import_batches"
    __table_args__ = (
        CheckConstraint("btrim(source_provider) <> ''", name="import_batch_provider_not_blank"),
        CheckConstraint(
            "btrim(original_filename) <> ''",
            name="import_batch_original_filename_not_blank",
        ),
        CheckConstraint("btrim(storage_key) <> ''", name="import_batch_storage_key_not_blank"),
        CheckConstraint("file_size_bytes > 0", name="import_batch_file_size_positive"),
        CheckConstraint(
            "sha256 ~ '^[0-9a-f]{64}$'",
            name="import_batch_sha256_format",
        ),
        CheckConstraint(
            "detected_format IS NULL OR btrim(detected_format) <> ''",
            name="import_batch_detected_format_not_blank",
        ),
        CheckConstraint(
            "detected_version IS NULL OR btrim(detected_version) <> ''",
            name="import_batch_detected_version_not_blank",
        ),
        CheckConstraint(
            "(reporting_period_start IS NULL AND reporting_period_end IS NULL) "
            "OR (reporting_period_start IS NOT NULL AND reporting_period_end IS NOT NULL "
            "AND reporting_period_start <= reporting_period_end)",
            name="import_batch_reporting_period",
        ),
        CheckConstraint(
            "total_rows >= 0 AND ready_rows >= 0 AND warning_rows >= 0 "
            "AND error_rows >= 0 AND duplicate_rows >= 0 AND excluded_rows >= 0",
            name="import_batch_counters_nonnegative",
        ),
        CheckConstraint(
            "error_summary IS NULL OR jsonb_typeof(error_summary) = 'object'",
            name="import_batch_error_summary_object",
        ),
        CheckConstraint(
            "reconciliation_summary IS NULL OR jsonb_typeof(reconciliation_summary) = 'object'",
            name="import_batch_reconciliation_summary_object",
        ),
        ForeignKeyConstraint(
            ["account_id", "portfolio_id"],
            ["accounts.id", "accounts.portfolio_id"],
            name="fk_import_batches_account_portfolio_accounts",
            ondelete="RESTRICT",
        ),
        Index(
            "uq_import_batches_account_sha256",
            "account_id",
            "sha256",
            unique=True,
        ),
        Index("ix_import_batches_portfolio_created_at", "portfolio_id", "created_at"),
        Index("ix_import_batches_status_created_at", "status", "created_at"),
    )

    id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid4)
    portfolio_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), nullable=False)
    account_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), nullable=False)
    source_provider: Mapped[str] = mapped_column(String(100), nullable=False)
    declared_format: Mapped[ImportFileFormat] = mapped_column(
        Enum(
            ImportFileFormat,
            name="import_file_format",
            native_enum=False,
            create_constraint=True,
            values_callable=_enum_values,
        ),
        nullable=False,
    )
    detected_format: Mapped[str | None] = mapped_column(String(100))
    detected_version: Mapped[str | None] = mapped_column(String(32))
    original_filename: Mapped[str] = mapped_column(String(255), nullable=False)
    storage_key: Mapped[str] = mapped_column(String(512), nullable=False, unique=True)
    file_size_bytes: Mapped[int] = mapped_column(BigInteger, nullable=False)
    sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    reporting_period_start: Mapped[date | None] = mapped_column(Date)
    reporting_period_end: Mapped[date | None] = mapped_column(Date)
    completeness: Mapped[ImportCompleteness] = mapped_column(
        Enum(
            ImportCompleteness,
            name="import_completeness",
            native_enum=False,
            create_constraint=True,
            values_callable=_enum_values,
        ),
        nullable=False,
        default=ImportCompleteness.UNKNOWN,
        server_default=ImportCompleteness.UNKNOWN.value,
    )
    status: Mapped[ImportStatus] = mapped_column(
        Enum(
            ImportStatus,
            name="import_status",
            native_enum=False,
            create_constraint=True,
            values_callable=_enum_values,
        ),
        nullable=False,
        default=ImportStatus.UPLOADED,
        server_default=ImportStatus.UPLOADED.value,
    )
    total_rows: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")
    ready_rows: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")
    warning_rows: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        default=0,
        server_default="0",
    )
    error_rows: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")
    duplicate_rows: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        default=0,
        server_default="0",
    )
    excluded_rows: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        default=0,
        server_default="0",
    )
    error_summary: Mapped[dict[str, object] | None] = mapped_column(JSONB)
    reconciliation_status: Mapped[ImportReconciliationStatus] = mapped_column(
        Enum(
            ImportReconciliationStatus,
            name="import_reconciliation_status",
            native_enum=False,
            create_constraint=True,
            values_callable=_enum_values,
        ),
        nullable=False,
        default=ImportReconciliationStatus.NOT_AVAILABLE,
        server_default=ImportReconciliationStatus.NOT_AVAILABLE.value,
    )
    reconciliation_summary: Mapped[dict[str, object] | None] = mapped_column(JSONB)

    rows: Mapped[list[ImportRowModel]] = relationship(
        back_populates="batch",
        cascade="all, delete-orphan",
        passive_deletes=True,
    )
    files: Mapped[list[ImportBatchFileModel]] = relationship(
        back_populates="batch",
        cascade="all, delete-orphan",
        passive_deletes=True,
        order_by="ImportBatchFileModel.sequence_number",
    )
    jobs: Mapped[list[ImportJobModel]] = relationship(
        back_populates="batch",
        cascade="all, delete-orphan",
        passive_deletes=True,
    )


class ImportBatchFileModel(TimestampMixin, Base):
    __tablename__ = "import_batch_files"
    __table_args__ = (
        CheckConstraint("sequence_number > 0", name="import_batch_file_sequence_positive"),
        CheckConstraint(
            "btrim(original_filename) <> ''",
            name="import_batch_file_original_filename_not_blank",
        ),
        CheckConstraint(
            "btrim(storage_key) <> ''",
            name="import_batch_file_storage_key_not_blank",
        ),
        CheckConstraint("file_size_bytes > 0", name="import_batch_file_size_positive"),
        CheckConstraint(
            "sha256 ~ '^[0-9a-f]{64}$'",
            name="import_batch_file_sha256_format",
        ),
        CheckConstraint(
            "detected_document_type IS NULL OR btrim(detected_document_type) <> ''",
            name="import_batch_file_document_type_not_blank",
        ),
        UniqueConstraint(
            "batch_id", "sequence_number", name="uq_import_batch_files_batch_sequence"
        ),
        Index("ix_import_batch_files_batch_id", "batch_id"),
    )

    id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid4)
    batch_id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("import_batches.id", ondelete="CASCADE"),
        nullable=False,
    )
    sequence_number: Mapped[int] = mapped_column(Integer, nullable=False)
    declared_format: Mapped[ImportFileFormat] = mapped_column(
        Enum(
            ImportFileFormat,
            name="import_file_format",
            native_enum=False,
            create_constraint=False,
            values_callable=_enum_values,
        ),
        nullable=False,
    )
    original_filename: Mapped[str] = mapped_column(String(255), nullable=False)
    storage_key: Mapped[str] = mapped_column(String(512), nullable=False, unique=True)
    file_size_bytes: Mapped[int] = mapped_column(BigInteger, nullable=False)
    sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    detected_document_type: Mapped[str | None] = mapped_column(String(100))

    batch: Mapped[ImportBatchModel] = relationship(back_populates="files")
    rows: Mapped[list[ImportRowModel]] = relationship(back_populates="source_file")


class ImportRowModel(TimestampMixin, Base):
    __tablename__ = "import_rows"
    __table_args__ = (
        CheckConstraint("sequence_number > 0", name="import_row_sequence_positive"),
        CheckConstraint(
            "source_page IS NULL OR source_page > 0",
            name="import_row_source_page_positive",
        ),
        CheckConstraint(
            "source_sheet IS NULL OR btrim(source_sheet) <> ''",
            name="import_row_source_sheet_not_blank",
        ),
        CheckConstraint(
            "source_row_number IS NULL OR source_row_number > 0",
            name="import_row_source_row_positive",
        ),
        CheckConstraint(
            "jsonb_typeof(raw_data) = 'object'",
            name="import_row_raw_data_object",
        ),
        CheckConstraint(
            "normalized_candidate IS NULL OR jsonb_typeof(normalized_candidate) = 'object'",
            name="import_row_candidate_object",
        ),
        CheckConstraint(
            "reconciliation_data IS NULL OR jsonb_typeof(reconciliation_data) = 'object'",
            name="import_row_reconciliation_data_object",
        ),
        CheckConstraint(
            "jsonb_typeof(warnings) = 'array'",
            name="import_row_warnings_array",
        ),
        CheckConstraint(
            "jsonb_typeof(errors) = 'array'",
            name="import_row_errors_array",
        ),
        CheckConstraint(
            "fingerprint IS NULL OR fingerprint ~ '^[0-9a-f]{64}$'",
            name="import_row_fingerprint_format",
        ),
        UniqueConstraint("batch_id", "sequence_number", name="uq_import_rows_batch_sequence"),
        UniqueConstraint("id", "batch_id", name="uq_import_rows_id_batch_id"),
        UniqueConstraint("matched_operation_id", name="uq_import_rows_matched_operation_id"),
        Index("ix_import_rows_batch_status", "batch_id", "status"),
        Index(
            "ix_import_rows_batch_fingerprint",
            "batch_id",
            "fingerprint",
            postgresql_where=text("fingerprint IS NOT NULL"),
        ),
    )

    id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid4)
    batch_id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("import_batches.id", ondelete="CASCADE"),
        nullable=False,
    )
    sequence_number: Mapped[int] = mapped_column(Integer, nullable=False)
    source_file_id: Mapped[UUID | None] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("import_batch_files.id", ondelete="RESTRICT"),
    )
    source_page: Mapped[int | None] = mapped_column(Integer)
    source_sheet: Mapped[str | None] = mapped_column(String(128))
    source_row_number: Mapped[int | None] = mapped_column(Integer)
    raw_data: Mapped[dict[str, object]] = mapped_column(JSONB, nullable=False)
    normalized_candidate: Mapped[dict[str, object] | None] = mapped_column(JSONB(none_as_null=True))
    reconciliation_data: Mapped[dict[str, object] | None] = mapped_column(JSONB(none_as_null=True))
    status: Mapped[ImportRowStatus] = mapped_column(
        Enum(
            ImportRowStatus,
            name="import_row_status",
            native_enum=False,
            create_constraint=True,
            values_callable=_enum_values,
        ),
        nullable=False,
        default=ImportRowStatus.PENDING,
        server_default=ImportRowStatus.PENDING.value,
    )
    warnings: Mapped[list[dict[str, object]]] = mapped_column(
        JSONB,
        nullable=False,
        default=list,
        server_default=text("'[]'::jsonb"),
    )
    errors: Mapped[list[dict[str, object]]] = mapped_column(
        JSONB,
        nullable=False,
        default=list,
        server_default=text("'[]'::jsonb"),
    )
    fingerprint: Mapped[str | None] = mapped_column(String(64))
    matched_operation_id: Mapped[UUID | None] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("operations.id", ondelete="RESTRICT"),
    )

    batch: Mapped[ImportBatchModel] = relationship(back_populates="rows")
    source_file: Mapped[ImportBatchFileModel | None] = relationship(back_populates="rows")
    resolutions: Mapped[list[ImportResolutionModel]] = relationship(
        back_populates="row",
        cascade="all, delete-orphan",
        passive_deletes=True,
    )


class ImportResolutionModel(TimestampMixin, Base):
    __tablename__ = "import_resolutions"
    __table_args__ = (
        CheckConstraint(
            "jsonb_typeof(payload) = 'object'",
            name="import_resolution_payload_object",
        ),
        CheckConstraint(
            "note IS NULL OR btrim(note) <> ''",
            name="import_resolution_note_not_blank",
        ),
        ForeignKeyConstraint(
            ["row_id", "batch_id"],
            ["import_rows.id", "import_rows.batch_id"],
            name="fk_import_resolutions_row_batch_import_rows",
            ondelete="CASCADE",
        ),
        UniqueConstraint(
            "row_id",
            "resolution_type",
            name="uq_import_resolutions_row_type",
        ),
        Index("ix_import_resolutions_batch_id", "batch_id"),
    )

    id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid4)
    batch_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), nullable=False)
    row_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), nullable=False)
    resolution_type: Mapped[ImportResolutionType] = mapped_column(
        Enum(
            ImportResolutionType,
            name="import_resolution_type",
            native_enum=False,
            create_constraint=True,
            values_callable=_enum_values,
        ),
        nullable=False,
    )
    source: Mapped[ImportResolutionSource] = mapped_column(
        Enum(
            ImportResolutionSource,
            name="import_resolution_source",
            native_enum=False,
            create_constraint=True,
            values_callable=_enum_values,
        ),
        nullable=False,
    )
    payload: Mapped[dict[str, object]] = mapped_column(JSONB, nullable=False)
    note: Mapped[str | None] = mapped_column(String(1000))

    row: Mapped[ImportRowModel] = relationship(back_populates="resolutions")


class ImportJobModel(TimestampMixin, Base):
    __tablename__ = "import_jobs"
    __table_args__ = (
        CheckConstraint("attempts >= 0", name="import_job_attempts_nonnegative"),
        CheckConstraint(
            "(status = 'running' AND locked_at IS NOT NULL AND locked_by IS NOT NULL) "
            "OR (status <> 'running' AND locked_at IS NULL AND locked_by IS NULL)",
            name="import_job_lock_state",
        ),
        CheckConstraint(
            "last_error IS NULL OR jsonb_typeof(last_error) = 'object'",
            name="import_job_last_error_object",
        ),
        CheckConstraint(
            "jsonb_typeof(payload) = 'object'",
            name="import_job_payload_object",
        ),
        CheckConstraint(
            "(job_type IN ('fx_sync', 'market_data_sync') AND batch_id IS NULL) "
            "OR (job_type NOT IN ('fx_sync', 'market_data_sync') AND batch_id IS NOT NULL)",
            name="import_job_scope",
        ),
        UniqueConstraint("batch_id", "job_type", name="uq_import_jobs_batch_type"),
        Index(
            "ix_import_jobs_claim",
            "status",
            "available_at",
            "created_at",
        ),
    )

    id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid4)
    batch_id: Mapped[UUID | None] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("import_batches.id", ondelete="CASCADE"),
        nullable=True,
    )
    job_type: Mapped[ImportJobType] = mapped_column(
        Enum(
            ImportJobType,
            name="import_job_type",
            native_enum=False,
            create_constraint=True,
            values_callable=_enum_values,
        ),
        nullable=False,
    )
    status: Mapped[ImportJobStatus] = mapped_column(
        Enum(
            ImportJobStatus,
            name="import_job_status",
            native_enum=False,
            create_constraint=True,
            values_callable=_enum_values,
        ),
        nullable=False,
        default=ImportJobStatus.PENDING,
        server_default=ImportJobStatus.PENDING.value,
    )
    attempts: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")
    available_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=text("now()"),
    )
    locked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    locked_by: Mapped[str | None] = mapped_column(String(200))
    payload: Mapped[dict[str, object]] = mapped_column(
        JSONB,
        nullable=False,
        default=dict,
        server_default=text("'{}'::jsonb"),
    )
    last_error: Mapped[dict[str, object] | None] = mapped_column(JSONB)

    batch: Mapped[ImportBatchModel | None] = relationship(back_populates="jobs")
