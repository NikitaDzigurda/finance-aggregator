"""Preserve exact manual prices while allowing independently identified observations."""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0016_market_price_provenance"
down_revision: str | Sequence[str] | None = "0015_remove_allocation_targets"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "market_prices",
        sa.Column("provider", sa.String(64), server_default="manual", nullable=False),
    )
    op.add_column(
        "market_prices",
        sa.Column("price_kind", sa.String(32), server_default="manual", nullable=False),
    )
    op.add_column(
        "market_prices",
        sa.Column("time_quality", sa.String(32), server_default="user_supplied", nullable=False),
    )
    op.add_column("market_prices", sa.Column("fetched_at", sa.DateTime(timezone=True)))
    op.execute("UPDATE market_prices SET fetched_at = created_at")
    op.alter_column("market_prices", "fetched_at", nullable=False)
    op.drop_constraint("market_price_source_manual", "market_prices", type_="check")
    op.create_check_constraint(
        "market_price_source_valid", "market_prices", "source IN ('manual', 'automatic')"
    )
    op.create_check_constraint(
        "market_price_provider_not_blank", "market_prices", "btrim(provider) <> ''"
    )
    op.create_check_constraint(
        "market_price_kind_not_blank", "market_prices", "btrim(price_kind) <> ''"
    )
    op.create_check_constraint(
        "market_price_time_quality_valid",
        "market_prices",
        "time_quality IN ('user_supplied', 'provider_snapshot', 'provider_trade')",
    )
    op.drop_constraint(
        "uq_market_prices_instrument_currency_observed_at", "market_prices", type_="unique"
    )
    op.create_unique_constraint(
        "uq_market_prices_observation_identity",
        "market_prices",
        ["instrument_id", "currency", "observed_at", "provider", "price_kind"],
    )
    op.add_column(
        "calculated_positions",
        sa.Column("market_price_observation_id", postgresql.UUID(as_uuid=True)),
    )
    op.create_foreign_key(
        "fk_calculated_positions_price_observation",
        "calculated_positions",
        "market_prices",
        ["market_price_observation_id"],
        ["id"],
        ondelete="RESTRICT",
    )


def downgrade() -> None:
    # Safe only before any automatic observation exists. Refuse destructive rollback.
    connection = op.get_bind()
    automatic_count = connection.scalar(
        sa.text("SELECT count(*) FROM market_prices WHERE source <> 'manual'")
    )
    if automatic_count:
        raise RuntimeError("Cannot downgrade after automatic market prices have been stored")
    op.drop_constraint(
        "fk_calculated_positions_price_observation",
        "calculated_positions",
        type_="foreignkey",
    )
    op.drop_column("calculated_positions", "market_price_observation_id")
    op.drop_constraint("uq_market_prices_observation_identity", "market_prices", type_="unique")
    op.create_unique_constraint(
        "uq_market_prices_instrument_currency_observed_at",
        "market_prices",
        ["instrument_id", "currency", "observed_at"],
    )
    op.drop_constraint("market_price_time_quality_valid", "market_prices", type_="check")
    op.drop_constraint("market_price_kind_not_blank", "market_prices", type_="check")
    op.drop_constraint("market_price_provider_not_blank", "market_prices", type_="check")
    op.drop_constraint("market_price_source_valid", "market_prices", type_="check")
    op.create_check_constraint("market_price_source_manual", "market_prices", "source = 'manual'")
    op.drop_column("market_prices", "fetched_at")
    op.drop_column("market_prices", "time_quality")
    op.drop_column("market_prices", "price_kind")
    op.drop_column("market_prices", "provider")
