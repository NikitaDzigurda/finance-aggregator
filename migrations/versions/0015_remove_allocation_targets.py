from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0015_remove_allocation_targets"
down_revision: str | Sequence[str] | None = "0014_allocation_targets"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.drop_index("ix_allocation_targets_category_id", table_name="allocation_targets")
    op.drop_table("allocation_targets")
    op.drop_table("allocation_target_plans")


def downgrade() -> None:
    op.create_table(
        "allocation_target_plans",
        sa.Column("portfolio_id", postgresql.UUID(as_uuid=True), nullable=False),
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
        sa.ForeignKeyConstraint(["portfolio_id"], ["portfolios.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("portfolio_id", name="pk_allocation_target_plans"),
    )
    op.create_table(
        "allocation_targets",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("portfolio_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("category_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("weight", sa.Numeric(precision=38, scale=24), nullable=False),
        sa.CheckConstraint(
            "weight >= 0 AND weight <= 1",
            name="allocation_target_weight_range",
        ),
        sa.ForeignKeyConstraint(
            ["portfolio_id"],
            ["allocation_target_plans.portfolio_id"],
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["category_id"],
            ["allocation_categories.id"],
            deferrable=True,
            initially="DEFERRED",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_allocation_targets"),
        sa.UniqueConstraint(
            "portfolio_id",
            "category_id",
            name="uq_allocation_targets_portfolio_category",
        ),
    )
    op.create_index(
        "ix_allocation_targets_category_id",
        "allocation_targets",
        ["category_id"],
    )
