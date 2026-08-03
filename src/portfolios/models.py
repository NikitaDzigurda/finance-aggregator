from __future__ import annotations

from typing import TYPE_CHECKING
from uuid import UUID, uuid4

from sqlalchemy import CheckConstraint, String, Uuid
from sqlalchemy.orm import Mapped, mapped_column, relationship

from shared.database import Base
from shared.models import TimestampMixin

if TYPE_CHECKING:
    from accounts.models import AccountModel


class PortfolioModel(TimestampMixin, Base):
    """A user-managed aggregation boundary with one reporting currency."""

    __tablename__ = "portfolios"
    __table_args__ = (
        CheckConstraint("btrim(name) <> ''", name="portfolio_name_not_blank"),
        CheckConstraint(
            "base_currency ~ '^[A-Z]{3}$'",
            name="portfolio_base_currency_format",
        ),
    )

    id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid4)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    base_currency: Mapped[str] = mapped_column(String(3), nullable=False)

    accounts: Mapped[list[AccountModel]] = relationship(
        back_populates="portfolio",
        cascade="all, delete-orphan",
        passive_deletes=True,
    )
