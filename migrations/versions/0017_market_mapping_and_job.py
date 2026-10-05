"""Add reviewed external market mappings and a global market-data job type."""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0017_market_mapping_and_job"
down_revision: str | Sequence[str] | None = "0016_market_price_provenance"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    if op.get_bind().scalar(
        sa.text("SELECT count(*) FROM market_prices WHERE source = 'automatic'")
    ):
        raise RuntimeError(
            "Automatic prices without reviewed mappings require manual reconciliation"
        )
    op.create_table(
        "market_mappings",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "instrument_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("instruments.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("provider", sa.String(64), nullable=False),
        sa.Column("market", sa.String(32), nullable=False),
        sa.Column("symbol", sa.String(64), nullable=False),
        sa.Column("quote_currency", sa.String(3), nullable=False),
        sa.Column("identifier_type", sa.String(32), nullable=False),
        sa.Column("identifier_value", sa.String(128), nullable=False),
        sa.Column("price_unit", sa.String(32), nullable=False),
        sa.Column("price_kind", sa.String(32), nullable=False),
        sa.Column("time_quality", sa.String(32), nullable=False),
        sa.Column("status", sa.String(32), nullable=False),
        sa.Column("confirmation_source", sa.String(200)),
        sa.Column("confirmed_at", sa.DateTime(timezone=True)),
        sa.Column("reason_code", sa.String(64)),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.CheckConstraint(
            "btrim(provider) <> '' AND btrim(market) <> '' AND btrim(symbol) <> ''",
            name="market_mapping_codes_not_blank",
        ),
        sa.CheckConstraint("quote_currency ~ '^[A-Z]{3}$'", name="market_mapping_currency_format"),
        sa.CheckConstraint(
            "identifier_type IN ('isin', 'crypto_asset_code')",
            name="market_mapping_identifier_type",
        ),
        sa.CheckConstraint(
            "status IN ('verified', 'ambiguous', 'unsupported')", name="market_mapping_status"
        ),
        sa.CheckConstraint(
            "price_unit IN ('security_unit', 'crypto_unit')", name="market_mapping_price_unit"
        ),
        sa.CheckConstraint("btrim(price_kind) <> ''", name="market_mapping_price_kind_not_blank"),
        sa.CheckConstraint(
            "time_quality IN ('provider_snapshot', 'provider_trade')",
            name="market_mapping_time_quality",
        ),
        sa.CheckConstraint(
            "(status = 'verified' AND confirmation_source IS NOT NULL "
            "AND confirmed_at IS NOT NULL AND reason_code IS NULL) "
            "OR (status <> 'verified' AND reason_code IS NOT NULL)",
            name="market_mapping_review_state",
        ),
        sa.UniqueConstraint(
            "instrument_id", "provider", "market", name="uq_market_mapping_instrument_market"
        ),
        sa.UniqueConstraint(
            "provider", "market", "symbol", "quote_currency", name="uq_market_mapping_external_code"
        ),
    )
    op.create_index("ix_market_mappings_instrument_id", "market_mappings", ["instrument_id"])
    op.add_column("market_prices", sa.Column("mapping_id", postgresql.UUID(as_uuid=True)))
    op.create_foreign_key(
        "fk_market_prices_mapping",
        "market_prices",
        "market_mappings",
        ["mapping_id"],
        ["id"],
        ondelete="RESTRICT",
    )
    op.create_check_constraint(
        "market_price_mapping_scope",
        "market_prices",
        "(source = 'manual' AND mapping_id IS NULL) "
        "OR (source = 'automatic' AND mapping_id IS NOT NULL)",
    )
    op.drop_constraint("import_job_type", "import_jobs", type_="check")
    op.create_check_constraint(
        "import_job_type",
        "import_jobs",
        "job_type IN ('parse_import', 'confirm_import', 'recalculate_portfolio', "
        "'rollback_import', 'fx_sync', 'market_data_sync')",
    )
    op.drop_constraint("import_job_scope", "import_jobs", type_="check")
    op.create_check_constraint(
        "import_job_scope",
        "import_jobs",
        "(job_type IN ('fx_sync', 'market_data_sync') AND batch_id IS NULL) "
        "OR (job_type NOT IN ('fx_sync', 'market_data_sync') AND batch_id IS NOT NULL)",
    )


def downgrade() -> None:
    connection = op.get_bind()
    if connection.scalar(sa.text("SELECT count(*) FROM market_prices WHERE source = 'automatic'")):
        raise RuntimeError("Cannot downgrade after automatic market prices have been stored")
    if connection.scalar(
        sa.text("SELECT count(*) FROM import_jobs WHERE job_type = 'market_data_sync'")
    ):
        raise RuntimeError("Cannot downgrade while market-data jobs exist")
    op.drop_constraint("import_job_scope", "import_jobs", type_="check")
    op.create_check_constraint(
        "import_job_scope",
        "import_jobs",
        "(job_type = 'fx_sync' AND batch_id IS NULL) "
        "OR (job_type <> 'fx_sync' AND batch_id IS NOT NULL)",
    )
    op.drop_constraint("import_job_type", "import_jobs", type_="check")
    op.create_check_constraint(
        "import_job_type",
        "import_jobs",
        "job_type IN ('parse_import', 'confirm_import', 'recalculate_portfolio', "
        "'rollback_import', 'fx_sync')",
    )
    op.drop_constraint("market_price_mapping_scope", "market_prices", type_="check")
    op.drop_constraint("fk_market_prices_mapping", "market_prices", type_="foreignkey")
    op.drop_column("market_prices", "mapping_id")
    op.drop_index("ix_market_mappings_instrument_id", table_name="market_mappings")
    op.drop_table("market_mappings")
