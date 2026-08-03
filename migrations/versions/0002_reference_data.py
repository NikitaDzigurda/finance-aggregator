"""Create portfolios, accounts, instruments, and identifiers.

Revision ID: 0002_reference_data
Revises: 0001_bootstrap
Create Date: 2026-08-03
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0002_reference_data"
down_revision: str | Sequence[str] | None = "0001_bootstrap"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Create the reference-data schema used by manual setup and imports."""
    op.create_table(
        "portfolios",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("name", sa.String(length=200), nullable=False),
        sa.Column("base_currency", sa.String(length=3), nullable=False),
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
        sa.CheckConstraint("btrim(name) <> ''", name="portfolio_name_not_blank"),
        sa.CheckConstraint(
            "base_currency ~ '^[A-Z]{3}$'",
            name="portfolio_base_currency_format",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_portfolios"),
    )

    op.create_table(
        "accounts",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("portfolio_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("name", sa.String(length=200), nullable=False),
        sa.Column(
            "account_type",
            sa.Enum(
                "broker",
                "bank",
                "cex",
                name="account_type",
                native_enum=False,
                create_constraint=True,
            ),
            nullable=False,
        ),
        sa.Column("institution_name", sa.String(length=200), nullable=True),
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
        sa.CheckConstraint("btrim(name) <> ''", name="account_name_not_blank"),
        sa.CheckConstraint(
            "institution_name IS NULL OR btrim(institution_name) <> ''",
            name="account_institution_name_not_blank",
        ),
        sa.ForeignKeyConstraint(
            ["portfolio_id"],
            ["portfolios.id"],
            name="fk_accounts_portfolio_id_portfolios",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_accounts"),
    )
    op.create_index("ix_accounts_portfolio_id", "accounts", ["portfolio_id"])

    op.create_table(
        "instruments",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("name", sa.String(length=200), nullable=False),
        sa.Column(
            "instrument_type",
            sa.Enum(
                "stock",
                "bond",
                "etf",
                "fund",
                "option",
                "crypto_asset",
                "currency",
                name="instrument_type",
                native_enum=False,
                create_constraint=True,
            ),
            nullable=False,
        ),
        sa.Column("currency", sa.String(length=3), nullable=False),
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
        sa.CheckConstraint("btrim(name) <> ''", name="instrument_name_not_blank"),
        sa.CheckConstraint("currency ~ '^[A-Z]{3}$'", name="instrument_currency_format"),
        sa.PrimaryKeyConstraint("id", name="pk_instruments"),
    )

    op.create_table(
        "instrument_identifiers",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("instrument_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column(
            "identifier_type",
            sa.Enum(
                "ticker",
                "isin",
                "provider_code",
                "crypto_asset_code",
                name="instrument_identifier_type",
                native_enum=False,
                create_constraint=True,
            ),
            nullable=False,
        ),
        sa.Column("value", sa.String(length=128), nullable=False),
        sa.Column("exchange", sa.String(length=32), nullable=True),
        sa.Column("provider", sa.String(length=64), nullable=True),
        sa.CheckConstraint("btrim(value) <> ''", name="instrument_identifier_value_not_blank"),
        sa.CheckConstraint(
            "(identifier_type = 'ticker' AND exchange IS NOT NULL AND provider IS NULL) "
            "OR (identifier_type = 'provider_code' AND provider IS NOT NULL "
            "AND exchange IS NULL) OR (identifier_type IN ('isin', 'crypto_asset_code') "
            "AND exchange IS NULL AND provider IS NULL)",
            name="instrument_identifier_scope",
        ),
        sa.ForeignKeyConstraint(
            ["instrument_id"],
            ["instruments.id"],
            name="fk_instrument_identifiers_instrument_id_instruments",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_instrument_identifiers"),
    )
    op.create_index(
        "ix_instrument_identifiers_instrument_id",
        "instrument_identifiers",
        ["instrument_id"],
    )
    op.create_index(
        "uq_instrument_identifiers_exact",
        "instrument_identifiers",
        ["instrument_id", "identifier_type", "value", "exchange", "provider"],
        unique=True,
        postgresql_nulls_not_distinct=True,
    )
    op.create_index(
        "uq_instrument_identifiers_isin",
        "instrument_identifiers",
        ["value"],
        unique=True,
        postgresql_where=sa.text("identifier_type = 'isin'"),
    )
    op.create_index(
        "uq_instrument_identifiers_ticker",
        "instrument_identifiers",
        ["value", "exchange"],
        unique=True,
        postgresql_where=sa.text("identifier_type = 'ticker'"),
    )
    op.create_index(
        "uq_instrument_identifiers_provider_code",
        "instrument_identifiers",
        ["provider", "value"],
        unique=True,
        postgresql_where=sa.text("identifier_type = 'provider_code'"),
    )
    op.create_index(
        "uq_instrument_identifiers_crypto_asset_code",
        "instrument_identifiers",
        ["value"],
        unique=True,
        postgresql_where=sa.text("identifier_type = 'crypto_asset_code'"),
    )


def downgrade() -> None:
    """Remove the reference-data schema in reverse dependency order."""
    op.drop_index(
        "uq_instrument_identifiers_crypto_asset_code",
        table_name="instrument_identifiers",
    )
    op.drop_index(
        "uq_instrument_identifiers_provider_code",
        table_name="instrument_identifiers",
    )
    op.drop_index("uq_instrument_identifiers_ticker", table_name="instrument_identifiers")
    op.drop_index("uq_instrument_identifiers_isin", table_name="instrument_identifiers")
    op.drop_index("uq_instrument_identifiers_exact", table_name="instrument_identifiers")
    op.drop_index(
        "ix_instrument_identifiers_instrument_id",
        table_name="instrument_identifiers",
    )
    op.drop_table("instrument_identifiers")
    op.drop_table("instruments")
    op.drop_index("ix_accounts_portfolio_id", table_name="accounts")
    op.drop_table("accounts")
    op.drop_table("portfolios")
