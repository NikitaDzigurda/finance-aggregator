from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from uuid import UUID, uuid4

from sqlalchemy import CheckConstraint, DateTime, ForeignKey, Index, String, UniqueConstraint, Uuid
from sqlalchemy.orm import Mapped, mapped_column

from shared.database import Base
from shared.db_types import PriceNumeric
from shared.models import TimestampMixin


class MarketPriceModel(TimestampMixin, Base):
    __tablename__ = "market_prices"
    __table_args__ = (
        CheckConstraint("price > 0", name="market_price_positive"),
        CheckConstraint(
            "currency ~ '^[A-Z]{3}$'",
            name="market_price_currency_format",
        ),
        UniqueConstraint(
            "instrument_id",
            "currency",
            "observed_at",
            name="uq_market_prices_instrument_currency_observed_at",
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
    price: Mapped[Decimal] = mapped_column(PriceNumeric(), nullable=False)
    currency: Mapped[str] = mapped_column(String(3), nullable=False)
    observed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
