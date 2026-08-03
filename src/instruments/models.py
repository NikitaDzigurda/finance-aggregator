from __future__ import annotations

from enum import StrEnum
from uuid import UUID, uuid4

from sqlalchemy import (
    CheckConstraint,
    Enum,
    ForeignKey,
    Index,
    String,
    Uuid,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from shared.database import Base
from shared.models import TimestampMixin


class InstrumentType(StrEnum):
    STOCK = "stock"
    BOND = "bond"
    ETF = "etf"
    FUND = "fund"
    OPTION = "option"
    CRYPTO_ASSET = "crypto_asset"
    CURRENCY = "currency"


class InstrumentIdentifierType(StrEnum):
    TICKER = "ticker"
    ISIN = "isin"
    PROVIDER_CODE = "provider_code"
    CRYPTO_ASSET_CODE = "crypto_asset_code"


def _enum_values(enum_class: type[StrEnum]) -> list[str]:
    return [member.value for member in enum_class]


class InstrumentModel(TimestampMixin, Base):
    """A globally reusable asset definition referenced by ledger operations."""

    __tablename__ = "instruments"
    __table_args__ = (
        CheckConstraint("btrim(name) <> ''", name="instrument_name_not_blank"),
        CheckConstraint("currency ~ '^[A-Z]{3}$'", name="instrument_currency_format"),
    )

    id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid4)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    instrument_type: Mapped[InstrumentType] = mapped_column(
        Enum(
            InstrumentType,
            name="instrument_type",
            native_enum=False,
            create_constraint=True,
            values_callable=_enum_values,
        ),
        nullable=False,
    )
    currency: Mapped[str] = mapped_column(String(3), nullable=False)

    identifiers: Mapped[list[InstrumentIdentifierModel]] = relationship(
        back_populates="instrument",
        cascade="all, delete-orphan",
        passive_deletes=True,
        lazy="selectin",
    )


class InstrumentIdentifierModel(Base):
    """A typed external identifier with optional exchange or provider scope."""

    __tablename__ = "instrument_identifiers"
    __table_args__ = (
        CheckConstraint(
            "btrim(value) <> ''",
            name="instrument_identifier_value_not_blank",
        ),
        CheckConstraint(
            "(identifier_type = 'ticker' AND exchange IS NOT NULL AND provider IS NULL) "
            "OR (identifier_type = 'provider_code' AND provider IS NOT NULL "
            "AND exchange IS NULL) OR (identifier_type IN ('isin', 'crypto_asset_code') "
            "AND exchange IS NULL AND provider IS NULL)",
            name="instrument_identifier_scope",
        ),
        Index(
            "uq_instrument_identifiers_exact",
            "instrument_id",
            "identifier_type",
            "value",
            "exchange",
            "provider",
            unique=True,
            postgresql_nulls_not_distinct=True,
        ),
        Index(
            "uq_instrument_identifiers_isin",
            "value",
            unique=True,
            postgresql_where=text("identifier_type = 'isin'"),
        ),
        Index(
            "uq_instrument_identifiers_ticker",
            "value",
            "exchange",
            unique=True,
            postgresql_where=text("identifier_type = 'ticker'"),
        ),
        Index(
            "uq_instrument_identifiers_provider_code",
            "provider",
            "value",
            unique=True,
            postgresql_where=text("identifier_type = 'provider_code'"),
        ),
        Index(
            "uq_instrument_identifiers_crypto_asset_code",
            "value",
            unique=True,
            postgresql_where=text("identifier_type = 'crypto_asset_code'"),
        ),
    )

    id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid4)
    instrument_id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("instruments.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    identifier_type: Mapped[InstrumentIdentifierType] = mapped_column(
        Enum(
            InstrumentIdentifierType,
            name="instrument_identifier_type",
            native_enum=False,
            create_constraint=True,
            values_callable=_enum_values,
        ),
        nullable=False,
    )
    value: Mapped[str] = mapped_column(String(128), nullable=False)
    exchange: Mapped[str | None] = mapped_column(String(32))
    provider: Mapped[str | None] = mapped_column(String(64))

    instrument: Mapped[InstrumentModel] = relationship(back_populates="identifiers")
