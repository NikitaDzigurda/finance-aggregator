from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0004_import_staging"
down_revision: str | Sequence[str] | None = "0003_operations"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "import_batches",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("portfolio_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("account_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("source_provider", sa.String(length=100), nullable=False),
        sa.Column(
            "declared_format",
            sa.Enum(
                "csv",
                "xlsx",
                "pdf",
                name="import_file_format",
                native_enum=False,
                create_constraint=True,
            ),
            nullable=False,
        ),
        sa.Column("detected_format", sa.String(length=100), nullable=True),
        sa.Column("detected_version", sa.String(length=32), nullable=True),
        sa.Column("original_filename", sa.String(length=255), nullable=False),
        sa.Column("storage_key", sa.String(length=512), nullable=False),
        sa.Column("file_size_bytes", sa.BigInteger(), nullable=False),
        sa.Column("sha256", sa.String(length=64), nullable=False),
        sa.Column("reporting_period_start", sa.Date(), nullable=True),
        sa.Column("reporting_period_end", sa.Date(), nullable=True),
        sa.Column(
            "completeness",
            sa.Enum(
                "complete",
                "partial",
                "snapshot",
                "unknown",
                name="import_completeness",
                native_enum=False,
                create_constraint=True,
            ),
            server_default="unknown",
            nullable=False,
        ),
        sa.Column(
            "status",
            sa.Enum(
                "uploaded",
                "detecting",
                "parsing",
                "awaiting_review",
                "ready_to_commit",
                "committing",
                "committed",
                "recalculating",
                "completed",
                "failed",
                "rolled_back",
                name="import_status",
                native_enum=False,
                create_constraint=True,
            ),
            server_default="uploaded",
            nullable=False,
        ),
        sa.Column("total_rows", sa.Integer(), server_default="0", nullable=False),
        sa.Column("ready_rows", sa.Integer(), server_default="0", nullable=False),
        sa.Column("warning_rows", sa.Integer(), server_default="0", nullable=False),
        sa.Column("error_rows", sa.Integer(), server_default="0", nullable=False),
        sa.Column("duplicate_rows", sa.Integer(), server_default="0", nullable=False),
        sa.Column("excluded_rows", sa.Integer(), server_default="0", nullable=False),
        sa.Column(
            "error_summary",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=True,
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "btrim(source_provider) <> ''",
            name="import_batch_provider_not_blank",
        ),
        sa.CheckConstraint(
            "btrim(original_filename) <> ''",
            name="import_batch_original_filename_not_blank",
        ),
        sa.CheckConstraint(
            "btrim(storage_key) <> ''",
            name="import_batch_storage_key_not_blank",
        ),
        sa.CheckConstraint("file_size_bytes > 0", name="import_batch_file_size_positive"),
        sa.CheckConstraint(
            "sha256 ~ '^[0-9a-f]{64}$'",
            name="import_batch_sha256_format",
        ),
        sa.CheckConstraint(
            "detected_format IS NULL OR btrim(detected_format) <> ''",
            name="import_batch_detected_format_not_blank",
        ),
        sa.CheckConstraint(
            "detected_version IS NULL OR btrim(detected_version) <> ''",
            name="import_batch_detected_version_not_blank",
        ),
        sa.CheckConstraint(
            "(reporting_period_start IS NULL AND reporting_period_end IS NULL) "
            "OR (reporting_period_start IS NOT NULL AND reporting_period_end IS NOT NULL "
            "AND reporting_period_start <= reporting_period_end)",
            name="import_batch_reporting_period",
        ),
        sa.CheckConstraint(
            "total_rows >= 0 AND ready_rows >= 0 AND warning_rows >= 0 "
            "AND error_rows >= 0 AND duplicate_rows >= 0 AND excluded_rows >= 0",
            name="import_batch_counters_nonnegative",
        ),
        sa.CheckConstraint(
            "error_summary IS NULL OR jsonb_typeof(error_summary) = 'object'",
            name="import_batch_error_summary_object",
        ),
        sa.ForeignKeyConstraint(
            ["account_id", "portfolio_id"],
            ["accounts.id", "accounts.portfolio_id"],
            name="fk_import_batches_account_portfolio_accounts",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_import_batches"),
        sa.UniqueConstraint("storage_key", name="uq_import_batches_storage_key"),
    )
    op.create_index(
        "uq_import_batches_account_sha256",
        "import_batches",
        ["account_id", "sha256"],
        unique=True,
    )
    op.create_index(
        "ix_import_batches_portfolio_created_at",
        "import_batches",
        ["portfolio_id", "created_at"],
    )
    op.create_index(
        "ix_import_batches_status_created_at",
        "import_batches",
        ["status", "created_at"],
    )

    op.create_table(
        "import_rows",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("batch_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("sequence_number", sa.Integer(), nullable=False),
        sa.Column("source_page", sa.Integer(), nullable=True),
        sa.Column("source_sheet", sa.String(length=128), nullable=True),
        sa.Column("source_row_number", sa.Integer(), nullable=True),
        sa.Column("raw_data", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column(
            "normalized_candidate",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=True,
        ),
        sa.Column(
            "status",
            sa.Enum(
                "pending",
                "ready",
                "warning",
                "error",
                "duplicate",
                "excluded",
                "committed",
                name="import_row_status",
                native_enum=False,
                create_constraint=True,
            ),
            server_default="pending",
            nullable=False,
        ),
        sa.Column(
            "warnings",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'[]'::jsonb"),
            nullable=False,
        ),
        sa.Column(
            "errors",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'[]'::jsonb"),
            nullable=False,
        ),
        sa.Column("fingerprint", sa.String(length=64), nullable=True),
        sa.Column("matched_operation_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint("sequence_number > 0", name="import_row_sequence_positive"),
        sa.CheckConstraint(
            "source_page IS NULL OR source_page > 0",
            name="import_row_source_page_positive",
        ),
        sa.CheckConstraint(
            "source_sheet IS NULL OR btrim(source_sheet) <> ''",
            name="import_row_source_sheet_not_blank",
        ),
        sa.CheckConstraint(
            "source_row_number IS NULL OR source_row_number > 0",
            name="import_row_source_row_positive",
        ),
        sa.CheckConstraint(
            "jsonb_typeof(raw_data) = 'object'",
            name="import_row_raw_data_object",
        ),
        sa.CheckConstraint(
            "normalized_candidate IS NULL OR jsonb_typeof(normalized_candidate) = 'object'",
            name="import_row_candidate_object",
        ),
        sa.CheckConstraint(
            "jsonb_typeof(warnings) = 'array'",
            name="import_row_warnings_array",
        ),
        sa.CheckConstraint(
            "jsonb_typeof(errors) = 'array'",
            name="import_row_errors_array",
        ),
        sa.CheckConstraint(
            "fingerprint IS NULL OR fingerprint ~ '^[0-9a-f]{64}$'",
            name="import_row_fingerprint_format",
        ),
        sa.ForeignKeyConstraint(
            ["batch_id"],
            ["import_batches.id"],
            name="fk_import_rows_batch_id_import_batches",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["matched_operation_id"],
            ["operations.id"],
            name="fk_import_rows_matched_operation_id_operations",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_import_rows"),
        sa.UniqueConstraint(
            "batch_id",
            "sequence_number",
            name="uq_import_rows_batch_sequence",
        ),
        sa.UniqueConstraint("id", "batch_id", name="uq_import_rows_id_batch_id"),
        sa.UniqueConstraint(
            "matched_operation_id",
            name="uq_import_rows_matched_operation_id",
        ),
    )
    op.create_index(
        "ix_import_rows_batch_status",
        "import_rows",
        ["batch_id", "status"],
    )
    op.create_index(
        "ix_import_rows_batch_fingerprint",
        "import_rows",
        ["batch_id", "fingerprint"],
        postgresql_where=sa.text("fingerprint IS NOT NULL"),
    )

    op.create_table(
        "import_resolutions",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("batch_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("row_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column(
            "resolution_type",
            sa.Enum(
                "instrument_match",
                "currency_selection",
                "exclude_row",
                "allow_duplicate",
                "operation_type_correction",
                name="import_resolution_type",
                native_enum=False,
                create_constraint=True,
            ),
            nullable=False,
        ),
        sa.Column(
            "source",
            sa.Enum(
                "user",
                "adapter",
                name="import_resolution_source",
                native_enum=False,
                create_constraint=True,
            ),
            nullable=False,
        ),
        sa.Column("payload", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("note", sa.String(length=1000), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "jsonb_typeof(payload) = 'object'",
            name="import_resolution_payload_object",
        ),
        sa.CheckConstraint(
            "note IS NULL OR btrim(note) <> ''",
            name="import_resolution_note_not_blank",
        ),
        sa.ForeignKeyConstraint(
            ["row_id", "batch_id"],
            ["import_rows.id", "import_rows.batch_id"],
            name="fk_import_resolutions_row_batch_import_rows",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_import_resolutions"),
        sa.UniqueConstraint(
            "row_id",
            "resolution_type",
            name="uq_import_resolutions_row_type",
        ),
    )
    op.create_index(
        "ix_import_resolutions_batch_id",
        "import_resolutions",
        ["batch_id"],
    )

    op.create_check_constraint(
        "operation_import_source_batch",
        "operations",
        "source_type = 'manual' OR import_batch_id IS NOT NULL",
    )
    op.create_foreign_key(
        "fk_operations_import_batch_id_import_batches",
        "operations",
        "import_batches",
        ["import_batch_id"],
        ["id"],
        ondelete="RESTRICT",
    )


def downgrade() -> None:
    op.drop_constraint(
        "fk_operations_import_batch_id_import_batches",
        "operations",
        type_="foreignkey",
    )
    op.drop_constraint(
        op.f("ck_operations_operation_import_source_batch"),
        "operations",
        type_="check",
    )
    op.drop_index("ix_import_resolutions_batch_id", table_name="import_resolutions")
    op.drop_table("import_resolutions")
    op.drop_index("ix_import_rows_batch_fingerprint", table_name="import_rows")
    op.drop_index("ix_import_rows_batch_status", table_name="import_rows")
    op.drop_table("import_rows")
    op.drop_index("ix_import_batches_status_created_at", table_name="import_batches")
    op.drop_index("ix_import_batches_portfolio_created_at", table_name="import_batches")
    op.drop_index("uq_import_batches_account_sha256", table_name="import_batches")
    op.drop_table("import_batches")
