from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0007_portfolio_calculation"
down_revision: str | Sequence[str] | None = "0006_import_deduplication"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "market_prices",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("instrument_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("price", sa.Numeric(precision=38, scale=18), nullable=False),
        sa.Column("currency", sa.String(length=3), nullable=False),
        sa.Column("observed_at", sa.DateTime(timezone=True), nullable=False),
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
        sa.CheckConstraint("price > 0", name="market_price_positive"),
        sa.CheckConstraint(
            "currency ~ '^[A-Z]{3}$'",
            name="market_price_currency_format",
        ),
        sa.ForeignKeyConstraint(
            ["instrument_id"],
            ["instruments.id"],
            name="fk_market_prices_instrument_id_instruments",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_market_prices"),
        sa.UniqueConstraint(
            "instrument_id",
            "currency",
            "observed_at",
            name="uq_market_prices_instrument_currency_observed_at",
        ),
    )
    op.create_index(
        "ix_market_prices_instrument_observed_at",
        "market_prices",
        ["instrument_id", "observed_at"],
    )
    op.create_table(
        "calculation_snapshots",
        sa.Column("portfolio_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column(
            "cost_basis_method",
            sa.Enum(
                "weighted_average",
                name="cost_basis_method",
                native_enum=False,
                create_constraint=True,
            ),
            nullable=False,
        ),
        sa.Column(
            "status",
            sa.Enum(
                "completed",
                "completed_with_diagnostics",
                name="calculation_status",
                native_enum=False,
                create_constraint=True,
            ),
            nullable=False,
        ),
        sa.Column("as_of", sa.DateTime(timezone=True), nullable=False),
        sa.Column("operation_count", sa.Integer(), nullable=False),
        sa.Column(
            "diagnostics",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'[]'::jsonb"),
            nullable=False,
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
            "operation_count >= 0",
            name="calculation_operation_count_nonnegative",
        ),
        sa.CheckConstraint(
            "jsonb_typeof(diagnostics) = 'array'",
            name="calculation_diagnostics_array",
        ),
        sa.ForeignKeyConstraint(
            ["portfolio_id"],
            ["portfolios.id"],
            name="fk_calculation_snapshots_portfolio_id_portfolios",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("portfolio_id", name="pk_calculation_snapshots"),
    )
    op.create_table(
        "calculated_positions",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("portfolio_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("account_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("instrument_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("quantity", sa.Numeric(precision=38, scale=18), nullable=False),
        sa.Column("cost_currency", sa.String(length=3), nullable=True),
        sa.Column("average_cost", sa.Numeric(precision=38, scale=18), nullable=True),
        sa.Column("cost_basis", sa.Numeric(precision=38, scale=18), nullable=True),
        sa.Column("valuation_currency", sa.String(length=3), nullable=True),
        sa.Column("market_price", sa.Numeric(precision=38, scale=18), nullable=True),
        sa.Column("market_value", sa.Numeric(precision=38, scale=18), nullable=True),
        sa.Column("realised_pnl", sa.Numeric(precision=38, scale=18), nullable=True),
        sa.Column("unrealised_pnl", sa.Numeric(precision=38, scale=18), nullable=True),
        sa.Column(
            "diagnostics",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'[]'::jsonb"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "cost_currency IS NULL OR cost_currency ~ '^[A-Z]{3}$'",
            name="calculated_position_cost_currency_format",
        ),
        sa.CheckConstraint(
            "valuation_currency IS NULL OR valuation_currency ~ '^[A-Z]{3}$'",
            name="calculated_position_valuation_currency_format",
        ),
        sa.CheckConstraint(
            "jsonb_typeof(diagnostics) = 'array'",
            name="calculated_position_diagnostics_array",
        ),
        sa.ForeignKeyConstraint(
            ["account_id", "portfolio_id"],
            ["accounts.id", "accounts.portfolio_id"],
            name="fk_calculated_positions_account_portfolio_accounts",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["instrument_id"],
            ["instruments.id"],
            name="fk_calculated_positions_instrument_id_instruments",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["portfolio_id"],
            ["calculation_snapshots.portfolio_id"],
            name="fk_calculated_positions_portfolio_id_calculation_snapshots",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_calculated_positions"),
        sa.UniqueConstraint(
            "portfolio_id",
            "account_id",
            "instrument_id",
            name="uq_calculated_positions_portfolio_account_instrument",
        ),
    )
    op.create_index(
        "ix_calculated_positions_portfolio_id",
        "calculated_positions",
        ["portfolio_id"],
    )
    op.create_table(
        "calculated_cash_balances",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("portfolio_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("account_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("currency", sa.String(length=3), nullable=False),
        sa.Column("amount", sa.Numeric(precision=38, scale=18), nullable=False),
        sa.CheckConstraint(
            "currency ~ '^[A-Z]{3}$'",
            name="calculated_cash_currency_format",
        ),
        sa.ForeignKeyConstraint(
            ["account_id", "portfolio_id"],
            ["accounts.id", "accounts.portfolio_id"],
            name="fk_calculated_cash_account_portfolio_accounts",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["portfolio_id"],
            ["calculation_snapshots.portfolio_id"],
            name="fk_calculated_cash_balances_portfolio_id_calculation_snapshots",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_calculated_cash_balances"),
        sa.UniqueConstraint(
            "portfolio_id",
            "account_id",
            "currency",
            name="uq_calculated_cash_portfolio_account_currency",
        ),
    )
    op.create_index(
        "ix_calculated_cash_balances_portfolio_id",
        "calculated_cash_balances",
        ["portfolio_id"],
    )
    op.create_table(
        "calculated_currency_metrics",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("portfolio_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("account_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("currency", sa.String(length=3), nullable=False),
        sa.Column("fees", sa.Numeric(precision=38, scale=18), nullable=False),
        sa.Column("taxes", sa.Numeric(precision=38, scale=18), nullable=False),
        sa.Column("income", sa.Numeric(precision=38, scale=18), nullable=False),
        sa.Column("realised_pnl", sa.Numeric(precision=38, scale=18), nullable=False),
        sa.CheckConstraint(
            "currency ~ '^[A-Z]{3}$'",
            name="calculated_metrics_currency_format",
        ),
        sa.CheckConstraint("fees >= 0", name="calculated_metrics_fees_nonnegative"),
        sa.CheckConstraint("taxes >= 0", name="calculated_metrics_taxes_nonnegative"),
        sa.CheckConstraint("income >= 0", name="calculated_metrics_income_nonnegative"),
        sa.ForeignKeyConstraint(
            ["account_id", "portfolio_id"],
            ["accounts.id", "accounts.portfolio_id"],
            name="fk_calculated_metrics_account_portfolio_accounts",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["portfolio_id"],
            ["calculation_snapshots.portfolio_id"],
            name="fk_calc_metrics_snapshot",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_calculated_currency_metrics"),
        sa.UniqueConstraint(
            "portfolio_id",
            "account_id",
            "currency",
            name="uq_calculated_metrics_portfolio_account_currency",
        ),
    )
    op.create_index(
        "ix_calculated_currency_metrics_portfolio_id",
        "calculated_currency_metrics",
        ["portfolio_id"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_calculated_currency_metrics_portfolio_id",
        table_name="calculated_currency_metrics",
    )
    op.drop_table("calculated_currency_metrics")
    op.drop_index(
        "ix_calculated_cash_balances_portfolio_id",
        table_name="calculated_cash_balances",
    )
    op.drop_table("calculated_cash_balances")
    op.drop_index(
        "ix_calculated_positions_portfolio_id",
        table_name="calculated_positions",
    )
    op.drop_table("calculated_positions")
    op.drop_table("calculation_snapshots")
    op.drop_index(
        "ix_market_prices_instrument_observed_at",
        table_name="market_prices",
    )
    op.drop_table("market_prices")
