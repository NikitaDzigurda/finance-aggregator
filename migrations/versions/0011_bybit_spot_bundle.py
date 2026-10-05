from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0011_bybit_spot_bundle"
down_revision: str | Sequence[str] | None = "0010_import_reconciliation"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "import_batch_files",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("batch_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("sequence_number", sa.Integer(), nullable=False),
        sa.Column("declared_format", sa.String(length=4), nullable=False),
        sa.Column("original_filename", sa.String(length=255), nullable=False),
        sa.Column("storage_key", sa.String(length=512), nullable=False),
        sa.Column("file_size_bytes", sa.BigInteger(), nullable=False),
        sa.Column("sha256", sa.String(length=64), nullable=False),
        sa.Column("detected_document_type", sa.String(length=100), nullable=True),
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
            "sequence_number > 0", name="import_batch_file_sequence_positive"
        ),
        sa.CheckConstraint(
            "declared_format IN ('csv', 'xlsx', 'xml', 'pdf')",
            name="import_batch_file_declared_format",
        ),
        sa.CheckConstraint(
            "btrim(original_filename) <> ''",
            name="import_batch_file_original_filename_not_blank",
        ),
        sa.CheckConstraint(
            "btrim(storage_key) <> ''",
            name="import_batch_file_storage_key_not_blank",
        ),
        sa.CheckConstraint(
            "file_size_bytes > 0", name="import_batch_file_size_positive"
        ),
        sa.CheckConstraint(
            "sha256 ~ '^[0-9a-f]{64}$'", name="import_batch_file_sha256_format"
        ),
        sa.CheckConstraint(
            "detected_document_type IS NULL OR btrim(detected_document_type) <> ''",
            name="import_batch_file_document_type_not_blank",
        ),
        sa.ForeignKeyConstraint(
            ["batch_id"],
            ["import_batches.id"],
            name="fk_import_batch_files_batch_id_import_batches",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_import_batch_files"),
        sa.UniqueConstraint(
            "batch_id",
            "sequence_number",
            name="uq_import_batch_files_batch_sequence",
        ),
        sa.UniqueConstraint("storage_key", name="uq_import_batch_files_storage_key"),
    )
    op.create_index(
        "ix_import_batch_files_batch_id", "import_batch_files", ["batch_id"]
    )
    op.execute(
        """
        INSERT INTO import_batch_files (
            id, batch_id, sequence_number, declared_format, original_filename,
            storage_key, file_size_bytes, sha256, created_at, updated_at
        )
        SELECT id, id, 1, declared_format, original_filename, storage_key,
               file_size_bytes, sha256, created_at, updated_at
        FROM import_batches
        """
    )

    op.add_column(
        "import_rows",
        sa.Column("source_file_id", postgresql.UUID(as_uuid=True), nullable=True),
    )
    op.execute("UPDATE import_rows SET source_file_id = batch_id")
    op.create_foreign_key(
        "fk_import_rows_source_file_id_import_batch_files",
        "import_rows",
        "import_batch_files",
        ["source_file_id"],
        ["id"],
        ondelete="RESTRICT",
    )

    op.add_column(
        "operations",
        sa.Column("secondary_instrument_id", postgresql.UUID(as_uuid=True), nullable=True),
    )
    op.create_foreign_key(
        "fk_operations_secondary_instrument_id_instruments",
        "operations",
        "instruments",
        ["secondary_instrument_id"],
        ["id"],
        ondelete="RESTRICT",
    )
    op.create_index(
        "ix_operations_secondary_instrument_occurred_at",
        "operations",
        ["secondary_instrument_id", "occurred_at"],
    )
    op.drop_constraint("operation_type", "operations", type_="check")
    op.create_check_constraint(
        "operation_type",
        "operations",
        "operation_type IN ('trade', 'crypto_trade', 'income', 'fee', 'tax', "
        "'cash_movement', 'currency_exchange', 'crypto_transfer', 'corporate_action', "
        "'bond_redemption', 'balance_adjustment')",
    )


def downgrade() -> None:
    op.drop_constraint("operation_type", "operations", type_="check")
    op.create_check_constraint(
        "operation_type",
        "operations",
        "operation_type IN ('trade', 'income', 'fee', 'tax', 'cash_movement', "
        "'currency_exchange', 'crypto_transfer', 'corporate_action', "
        "'bond_redemption', 'balance_adjustment')",
    )
    op.drop_index(
        "ix_operations_secondary_instrument_occurred_at", table_name="operations"
    )
    op.drop_constraint(
        "fk_operations_secondary_instrument_id_instruments",
        "operations",
        type_="foreignkey",
    )
    op.drop_column("operations", "secondary_instrument_id")

    op.drop_constraint(
        "fk_import_rows_source_file_id_import_batch_files",
        "import_rows",
        type_="foreignkey",
    )
    op.drop_column("import_rows", "source_file_id")
    op.drop_index("ix_import_batch_files_batch_id", table_name="import_batch_files")
    op.drop_table("import_batch_files")
