from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from enum import StrEnum
from uuid import UUID, uuid4

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    Enum,
    ForeignKey,
    Index,
    String,
    UniqueConstraint,
    Uuid,
)
from sqlalchemy.orm import Mapped, mapped_column

from shared.database import Base
from shared.db_types import PriceNumeric, RateNumeric
from shared.models import TimestampMixin


class ExchangeRateMode(StrEnum):
    AUTOMATIC = "automatic"
    MANUAL = "manual"


class MarketMappingStatus(StrEnum):
    VERIFIED = "verified"
    AMBIGUOUS = "ambiguous"
    UNSUPPORTED = "unsupported"


class MarketPriceUnit(StrEnum):
    SECURITY_UNIT = "security_unit"
    CRYPTO_UNIT = "crypto_unit"


def _enum_values(enum_class: type[StrEnum]) -> list[str]:
    return [member.value for member in enum_class]


class MarketMappingModel(TimestampMixin, Base):
    """Reviewed, global link between a canonical asset and an external market code."""

    __tablename__ = "market_mappings"
    __table_args__ = (
        CheckConstraint(
            "btrim(provider) <> '' AND btrim(market) <> '' AND btrim(symbol) <> ''",
            name="market_mapping_codes_not_blank",
        ),
        CheckConstraint("quote_currency ~ '^[A-Z]{3}$'", name="market_mapping_currency_format"),
        CheckConstraint(
            "identifier_type IN ('isin', 'crypto_asset_code')",
            name="market_mapping_identifier_type",
        ),
        CheckConstraint(
            "status IN ('verified', 'ambiguous', 'unsupported')", name="market_mapping_status"
        ),
        CheckConstraint(
            "price_unit IN ('security_unit', 'crypto_unit')", name="market_mapping_price_unit"
        ),
        CheckConstraint("btrim(price_kind) <> ''", name="market_mapping_price_kind_not_blank"),
        CheckConstraint(
            "time_quality IN ('provider_snapshot', 'provider_trade')",
            name="market_mapping_time_quality",
        ),
        CheckConstraint(
            "(status = 'verified' AND confirmation_source IS NOT NULL "
            "AND confirmed_at IS NOT NULL AND reason_code IS NULL) "
            "OR (status <> 'verified' AND reason_code IS NOT NULL)",
            name="market_mapping_review_state",
        ),
        UniqueConstraint(
            "instrument_id", "provider", "market", name="uq_market_mapping_instrument_market"
        ),
        UniqueConstraint(
            "provider", "market", "symbol", "quote_currency", name="uq_market_mapping_external_code"
        ),
    )

    id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid4)
    instrument_id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("instruments.id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )
    provider: Mapped[str] = mapped_column(String(64), nullable=False)
    market: Mapped[str] = mapped_column(String(32), nullable=False)
    symbol: Mapped[str] = mapped_column(String(64), nullable=False)
    quote_currency: Mapped[str] = mapped_column(String(3), nullable=False)
    identifier_type: Mapped[str] = mapped_column(String(32), nullable=False)
    identifier_value: Mapped[str] = mapped_column(String(128), nullable=False)
    price_unit: Mapped[str] = mapped_column(String(32), nullable=False)
    price_kind: Mapped[str] = mapped_column(String(32), nullable=False)
    time_quality: Mapped[str] = mapped_column(String(32), nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False)
    confirmation_source: Mapped[str | None] = mapped_column(String(200))
    confirmed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    reason_code: Mapped[str | None] = mapped_column(String(64))


class MarketPriceModel(TimestampMixin, Base):
    __tablename__ = "market_prices"
    __table_args__ = (
        CheckConstraint("price > 0", name="market_price_positive"),
        CheckConstraint(
            "currency ~ '^[A-Z]{3}$'",
            name="market_price_currency_format",
        ),
        CheckConstraint("source IN ('manual', 'automatic')", name="market_price_source_valid"),
        CheckConstraint(
            "(source = 'manual' AND mapping_id IS NULL) "
            "OR (source = 'automatic' AND mapping_id IS NOT NULL)",
            name="market_price_mapping_scope",
        ),
        CheckConstraint("btrim(provider) <> ''", name="market_price_provider_not_blank"),
        CheckConstraint("btrim(price_kind) <> ''", name="market_price_kind_not_blank"),
        CheckConstraint(
            "time_quality IN ('user_supplied', 'provider_snapshot', 'provider_trade')",
            name="market_price_time_quality_valid",
        ),
        UniqueConstraint(
            "instrument_id",
            "currency",
            "observed_at",
            "provider",
            "price_kind",
            name="uq_market_prices_observation_identity",
        ),
        Index(
            "ix_market_prices_instrument_observed_at",
            "instrument_id",
            "observed_at",
        ),
    )

    id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid4)
    instrument_id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("instruments.id", ondelete="RESTRICT"),
        nullable=False,
    )
    mapping_id: Mapped[UUID | None] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("market_mappings.id", ondelete="RESTRICT"), nullable=True
    )
    price: Mapped[Decimal] = mapped_column(PriceNumeric(), nullable=False)
    currency: Mapped[str] = mapped_column(String(3), nullable=False)
    observed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    fetched_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    source: Mapped[str] = mapped_column(
        String(32), nullable=False, default="manual", server_default="manual"
    )
    provider: Mapped[str] = mapped_column(
        String(64), nullable=False, default="manual", server_default="manual"
    )
    price_kind: Mapped[str] = mapped_column(
        String(32), nullable=False, default="manual", server_default="manual"
    )
    time_quality: Mapped[str] = mapped_column(
        String(32), nullable=False, default="user_supplied", server_default="user_supplied"
    )


class ExchangeRateModel(TimestampMixin, Base):
    __tablename__ = "exchange_rates"
    __table_args__ = (
        CheckConstraint("rate > 0", name="exchange_rate_positive"),
        CheckConstraint(
            "base_currency ~ '^[A-Z]{3}$' AND quote_currency ~ '^[A-Z]{3}$'",
            name="exchange_rate_currency_format",
        ),
        CheckConstraint(
            "base_currency <> quote_currency",
            name="exchange_rate_currencies_different",
        ),
        CheckConstraint("btrim(provider) <> ''", name="exchange_rate_provider_not_blank"),
        CheckConstraint("btrim(source) <> ''", name="exchange_rate_source_not_blank"),
        UniqueConstraint(
            "provider",
            "base_currency",
            "quote_currency",
            "observed_at",
            name="uq_exchange_rates_provider_pair_observed_at",
        ),
        Index(
            "ix_exchange_rates_pair_observed_at",
            "base_currency",
            "quote_currency",
            "observed_at",
        ),
    )

    id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid4)
    base_currency: Mapped[str] = mapped_column(String(3), nullable=False)
    quote_currency: Mapped[str] = mapped_column(String(3), nullable=False)
    rate: Mapped[Decimal] = mapped_column(RateNumeric(), nullable=False)
    observed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    fetched_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    provider: Mapped[str] = mapped_column(String(64), nullable=False)
    source: Mapped[str] = mapped_column(String(64), nullable=False)
    mode: Mapped[ExchangeRateMode] = mapped_column(
        Enum(
            ExchangeRateMode,
            name="exchange_rate_mode",
            native_enum=False,
            create_constraint=True,
            values_callable=_enum_values,
        ),
        nullable=False,
    )
