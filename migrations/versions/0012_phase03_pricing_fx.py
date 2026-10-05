from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0012_phase03_pricing_fx"
down_revision: str | Sequence[str] | None = "0011_bybit_spot_bundle"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "market_prices",
        sa.Column("source", sa.String(length=32), server_default="manual", nullable=False),
    )
    op.create_check_constraint(
        "market_price_source_manual",
        "market_prices",
        "source = 'manual'",
    )

    op.create_table(
        "exchange_rates",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("base_currency", sa.String(length=3), nullable=False),
        sa.Column("quote_currency", sa.String(length=3), nullable=False),
        sa.Column("rate", sa.Numeric(precision=38, scale=24), nullable=False),
        sa.Column("observed_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("fetched_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("provider", sa.String(length=64), nullable=False),
        sa.Column("source", sa.String(length=64), nullable=False),
        sa.Column(
            "mode",
            sa.Enum(
                "automatic",
                "manual",
                name="exchange_rate_mode",
                native_enum=False,
                create_constraint=True,
            ),
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
        sa.CheckConstraint("rate > 0", name="exchange_rate_positive"),
        sa.CheckConstraint(
            "base_currency ~ '^[A-Z]{3}$' AND quote_currency ~ '^[A-Z]{3}$'",
            name="exchange_rate_currency_format",
        ),
        sa.CheckConstraint(
            "base_currency <> quote_currency",
            name="exchange_rate_currencies_different",
        ),
        sa.CheckConstraint(
            "btrim(provider) <> ''",
            name="exchange_rate_provider_not_blank",
        ),
        sa.CheckConstraint(
            "btrim(source) <> ''",
            name="exchange_rate_source_not_blank",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_exchange_rates"),
        sa.UniqueConstraint(
            "provider",
            "base_currency",
            "quote_currency",
            "observed_at",
            name="uq_exchange_rates_provider_pair_observed_at",
        ),
    )
    op.create_index(
        "ix_exchange_rates_pair_observed_at",
        "exchange_rates",
        ["base_currency", "quote_currency", "observed_at"],
    )

    op.drop_constraint("uq_import_jobs_batch_type", "import_jobs", type_="unique")
    op.drop_constraint("import_job_type", "import_jobs", type_="check")
    op.alter_column("import_jobs", "batch_id", existing_type=postgresql.UUID(), nullable=True)
    op.add_column(
        "import_jobs",
        sa.Column(
            "payload",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'{}'::jsonb"),
            nullable=False,
        ),
    )
    op.create_check_constraint(
        "import_job_type",
        "import_jobs",
        "job_type IN ('parse_import', 'confirm_import', 'recalculate_portfolio', "
        "'rollback_import', 'fx_sync')",
    )
    op.create_check_constraint(
        "import_job_payload_object",
        "import_jobs",
        "jsonb_typeof(payload) = 'object'",
    )
    op.create_check_constraint(
        "import_job_scope",
        "import_jobs",
        "(job_type = 'fx_sync' AND batch_id IS NULL) "
        "OR (job_type <> 'fx_sync' AND batch_id IS NOT NULL)",
    )
    op.create_unique_constraint(
        "uq_import_jobs_batch_type", "import_jobs", ["batch_id", "job_type"]
    )


def downgrade() -> None:
    op.drop_constraint("uq_import_jobs_batch_type", "import_jobs", type_="unique")
    op.drop_constraint("import_job_scope", "import_jobs", type_="check")
    op.drop_constraint("import_job_payload_object", "import_jobs", type_="check")
    op.drop_constraint("import_job_type", "import_jobs", type_="check")
    op.execute("DELETE FROM import_jobs WHERE job_type = 'fx_sync'")
    op.drop_column("import_jobs", "payload")
    op.alter_column("import_jobs", "batch_id", existing_type=postgresql.UUID(), nullable=False)
    op.create_check_constraint(
        "import_job_type",
        "import_jobs",
        "job_type IN ('parse_import', 'confirm_import', 'recalculate_portfolio', "
        "'rollback_import')",
    )
    op.create_unique_constraint(
        "uq_import_jobs_batch_type", "import_jobs", ["batch_id", "job_type"]
    )

    op.drop_index("ix_exchange_rates_pair_observed_at", table_name="exchange_rates")
    op.drop_table("exchange_rates")
    op.drop_constraint("market_price_source_manual", "market_prices", type_="check")
    op.drop_column("market_prices", "source")
