from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0003_operations"
down_revision: str | Sequence[str] | None = "0002_reference_data"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_unique_constraint(
        "uq_accounts_id_portfolio_id",
        "accounts",
        ["id", "portfolio_id"],
    )
    op.create_table(
        "operations",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("portfolio_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("account_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column(
            "operation_type",
            sa.Enum(
                "trade",
                "income",
                "fee",
                "tax",
                "cash_movement",
                "currency_exchange",
                "crypto_transfer",
                "corporate_action",
                "bond_redemption",
                "balance_adjustment",
                name="operation_type",
                native_enum=False,
                create_constraint=True,
            ),
            nullable=False,
        ),
        sa.Column("occurred_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column(
            "time_precision",
            sa.Enum(
                "date",
                "minute",
                "second",
                "millisecond",
                "microsecond",
                name="operation_time_precision",
                native_enum=False,
                create_constraint=True,
            ),
            nullable=False,
        ),
        sa.Column(
            "source_type",
            sa.Enum(
                "manual",
                "csv_import",
                "xlsx_import",
                "pdf_import",
                name="operation_source_type",
                native_enum=False,
                create_constraint=True,
            ),
            nullable=False,
        ),
        sa.Column("import_batch_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("source_operation_id", sa.String(length=256), nullable=True),
        sa.Column("source_row_number", sa.Integer(), nullable=True),
        sa.Column("payload", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("instrument_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column(
            "correction_of_operation_id",
            postgresql.UUID(as_uuid=True),
            nullable=True,
        ),
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
            name="operation_payload_object",
        ),
        sa.CheckConstraint(
            "source_row_number IS NULL OR source_row_number > 0",
            name="operation_source_row_positive",
        ),
        sa.CheckConstraint(
            "source_type <> 'manual' OR (import_batch_id IS NULL "
            "AND source_operation_id IS NULL AND source_row_number IS NULL)",
            name="operation_manual_source_fields",
        ),
        sa.CheckConstraint(
            "correction_of_operation_id IS NULL OR correction_of_operation_id <> id",
            name="operation_not_self_correction",
        ),
        sa.ForeignKeyConstraint(
            ["account_id", "portfolio_id"],
            ["accounts.id", "accounts.portfolio_id"],
            name="fk_operations_account_portfolio_accounts",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["instrument_id"],
            ["instruments.id"],
            name="fk_operations_instrument_id_instruments",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["correction_of_operation_id"],
            ["operations.id"],
            name="fk_operations_correction_of_operation_id_operations",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_operations"),
    )
    op.create_index(
        "ix_operations_portfolio_occurred_at",
        "operations",
        ["portfolio_id", "occurred_at"],
    )
    op.create_index(
        "ix_operations_account_occurred_at",
        "operations",
        ["account_id", "occurred_at"],
    )
    op.create_index(
        "ix_operations_type_occurred_at",
        "operations",
        ["operation_type", "occurred_at"],
    )
    op.create_index(
        "ix_operations_instrument_occurred_at",
        "operations",
        ["instrument_id", "occurred_at"],
    )


def downgrade() -> None:
    op.drop_index("ix_operations_instrument_occurred_at", table_name="operations")
    op.drop_index("ix_operations_type_occurred_at", table_name="operations")
    op.drop_index("ix_operations_account_occurred_at", table_name="operations")
    op.drop_index("ix_operations_portfolio_occurred_at", table_name="operations")
    op.drop_table("operations")
    op.drop_constraint("uq_accounts_id_portfolio_id", "accounts", type_="unique")
