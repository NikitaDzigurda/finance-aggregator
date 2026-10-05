from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0013_allocation_categories"
down_revision: str | Sequence[str] | None = "0012_phase03_pricing_fx"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_SYSTEM_CATEGORIES = (
    ("10000000-0000-4000-8000-000000000001", "Equity", "equity"),
    ("10000000-0000-4000-8000-000000000002", "Fixed income", "fixed_income"),
    ("10000000-0000-4000-8000-000000000003", "Fund", "fund"),
    ("10000000-0000-4000-8000-000000000004", "Derivative", "derivative"),
    ("10000000-0000-4000-8000-000000000005", "Crypto", "crypto"),
    ("10000000-0000-4000-8000-000000000006", "Cash", "cash"),
    ("10000000-0000-4000-8000-000000000007", "Other", "other"),
)


def upgrade() -> None:
    asset_class = sa.Enum(
        "equity",
        "fixed_income",
        "fund",
        "derivative",
        "crypto",
        "cash",
        "other",
        name="allocation_asset_class",
        native_enum=False,
        create_constraint=True,
    )
    op.create_table(
        "allocation_categories",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("portfolio_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("name", sa.String(length=100), nullable=False),
        sa.Column("system_class", asset_class, nullable=False),
        sa.Column("is_system", sa.Boolean(), nullable=False),
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
        sa.CheckConstraint("btrim(name) <> ''", name="allocation_category_name_not_blank"),
        sa.CheckConstraint(
            "(is_system AND portfolio_id IS NULL) OR (NOT is_system AND portfolio_id IS NOT NULL)",
            name="allocation_category_scope",
        ),
        sa.ForeignKeyConstraint(["portfolio_id"], ["portfolios.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id", name="pk_allocation_categories"),
    )
    op.create_index(
        "ix_allocation_categories_portfolio_id",
        "allocation_categories",
        ["portfolio_id"],
    )
    op.create_index(
        "uq_allocation_categories_system_class",
        "allocation_categories",
        ["system_class"],
        unique=True,
        postgresql_where=sa.text("is_system"),
    )
    op.create_index(
        "uq_allocation_categories_portfolio_name",
        "allocation_categories",
        ["portfolio_id", sa.text("lower(name)")],
        unique=True,
        postgresql_where=sa.text("NOT is_system"),
    )
    category_table = sa.table(
        "allocation_categories",
        sa.column("id", postgresql.UUID(as_uuid=True)),
        sa.column("portfolio_id", postgresql.UUID(as_uuid=True)),
        sa.column("name", sa.String()),
        sa.column("system_class", sa.String()),
        sa.column("is_system", sa.Boolean()),
    )
    op.bulk_insert(
        category_table,
        [
            {
                "id": category_id,
                "portfolio_id": None,
                "name": name,
                "system_class": system_class,
                "is_system": True,
            }
            for category_id, name, system_class in _SYSTEM_CATEGORIES
        ],
    )

    op.create_table(
        "instrument_category_overrides",
        sa.Column("portfolio_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("instrument_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("category_id", postgresql.UUID(as_uuid=True), nullable=False),
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
        sa.ForeignKeyConstraint(["instrument_id"], ["instruments.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(
            ["category_id"],
            ["allocation_categories.id"],
            deferrable=True,
            initially="DEFERRED",
        ),
        sa.PrimaryKeyConstraint(
            "portfolio_id",
            "instrument_id",
            name="pk_instrument_category_overrides",
        ),
    )
    op.create_index(
        "ix_instrument_category_overrides_category_id",
        "instrument_category_overrides",
        ["category_id"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_instrument_category_overrides_category_id",
        table_name="instrument_category_overrides",
    )
    op.drop_table("instrument_category_overrides")
    op.drop_index(
        "uq_allocation_categories_portfolio_name",
        table_name="allocation_categories",
    )
    op.drop_index(
        "uq_allocation_categories_system_class",
        table_name="allocation_categories",
    )
    op.drop_index(
        "ix_allocation_categories_portfolio_id",
        table_name="allocation_categories",
    )
    op.drop_table("allocation_categories")
