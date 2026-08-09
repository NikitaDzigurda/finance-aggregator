from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0006_import_deduplication"
down_revision: str | Sequence[str] | None = "0005_import_jobs"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("operations", sa.Column("fingerprint", sa.String(length=64), nullable=True))
    op.add_column(
        "operations",
        sa.Column("deduplication_key", sa.String(length=64), nullable=True),
    )
    op.create_check_constraint(
        "operation_source_fingerprint",
        "operations",
        "(source_type = 'manual' AND fingerprint IS NULL AND deduplication_key IS NULL) OR "
        "(source_type <> 'manual' AND fingerprint IS NOT NULL "
        "AND deduplication_key IS NOT NULL)",
    )
    op.create_check_constraint(
        "operation_fingerprint_format",
        "operations",
        "fingerprint IS NULL OR fingerprint ~ '^[0-9a-f]{64}$'",
    )
    op.create_check_constraint(
        "operation_deduplication_key_format",
        "operations",
        "deduplication_key IS NULL OR deduplication_key ~ '^[0-9a-f]{64}$'",
    )
    op.create_index(
        "ix_operations_account_fingerprint",
        "operations",
        ["account_id", "fingerprint"],
    )
    op.create_index(
        "uq_operations_account_deduplication_key",
        "operations",
        ["account_id", "deduplication_key"],
        unique=True,
        postgresql_where=sa.text("deduplication_key IS NOT NULL"),
    )


def downgrade() -> None:
    op.drop_index("uq_operations_account_deduplication_key", table_name="operations")
    op.drop_index("ix_operations_account_fingerprint", table_name="operations")
    op.drop_constraint(
        op.f("ck_operations_operation_deduplication_key_format"),
        "operations",
        type_="check",
    )
    op.drop_constraint(
        op.f("ck_operations_operation_fingerprint_format"),
        "operations",
        type_="check",
    )
    op.drop_constraint(
        op.f("ck_operations_operation_source_fingerprint"),
        "operations",
        type_="check",
    )
    op.drop_column("operations", "deduplication_key")
    op.drop_column("operations", "fingerprint")
