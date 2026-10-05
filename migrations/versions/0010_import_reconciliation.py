from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0010_import_reconciliation"
down_revision: str | Sequence[str] | None = "0009_xml_operation_source"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.drop_constraint("import_completeness", "import_batches", type_="check")
    op.alter_column(
        "import_batches",
        "completeness",
        existing_type=sa.String(length=8),
        type_=sa.String(length=23),
        existing_nullable=False,
    )
    op.execute(
        """
        UPDATE import_batches
        SET completeness = CASE completeness
            WHEN 'complete' THEN 'full_ledger'
            WHEN 'partial' THEN 'period_ledger'
            WHEN 'snapshot' THEN 'snapshot_with_movements'
            ELSE 'unknown'
        END
        """
    )
    op.create_check_constraint(
        "import_completeness",
        "import_batches",
        "completeness IN "
        "('full_ledger', 'period_ledger', 'snapshot_with_movements', 'unknown')",
    )
    op.add_column(
        "import_batches",
        sa.Column(
            "reconciliation_status",
            sa.String(length=13),
            server_default="not_available",
            nullable=False,
        ),
    )
    op.add_column(
        "import_batches",
        sa.Column(
            "reconciliation_summary",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=True,
        ),
    )
    op.create_check_constraint(
        "import_reconciliation_status",
        "import_batches",
        "reconciliation_status IN ('not_available', 'matched', 'mismatch')",
    )
    op.create_check_constraint(
        "import_batch_reconciliation_summary_object",
        "import_batches",
        "reconciliation_summary IS NULL "
        "OR jsonb_typeof(reconciliation_summary) = 'object'",
    )
    op.add_column(
        "import_rows",
        sa.Column(
            "reconciliation_data",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=True,
        ),
    )
    op.create_check_constraint(
        "import_row_reconciliation_data_object",
        "import_rows",
        "reconciliation_data IS NULL OR jsonb_typeof(reconciliation_data) = 'object'",
    )


def downgrade() -> None:
    op.drop_constraint(
        "import_row_reconciliation_data_object", "import_rows", type_="check"
    )
    op.drop_column("import_rows", "reconciliation_data")
    op.drop_constraint(
        "import_batch_reconciliation_summary_object", "import_batches", type_="check"
    )
    op.drop_constraint("import_reconciliation_status", "import_batches", type_="check")
    op.drop_column("import_batches", "reconciliation_summary")
    op.drop_column("import_batches", "reconciliation_status")
    op.drop_constraint("import_completeness", "import_batches", type_="check")
    op.execute(
        """
        UPDATE import_batches
        SET completeness = CASE completeness
            WHEN 'full_ledger' THEN 'complete'
            WHEN 'period_ledger' THEN 'partial'
            WHEN 'snapshot_with_movements' THEN 'snapshot'
            ELSE 'unknown'
        END
        """
    )
    op.alter_column(
        "import_batches",
        "completeness",
        existing_type=sa.String(length=23),
        type_=sa.String(length=8),
        existing_nullable=False,
    )
    op.create_check_constraint(
        "import_completeness",
        "import_batches",
        "completeness IN ('complete', 'partial', 'snapshot', 'unknown')",
    )
