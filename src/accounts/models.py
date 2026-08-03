from __future__ import annotations

from enum import StrEnum
from typing import TYPE_CHECKING
from uuid import UUID, uuid4

from sqlalchemy import CheckConstraint, Enum, ForeignKey, String, Uuid
from sqlalchemy.orm import Mapped, mapped_column, relationship

from shared.database import Base
from shared.models import TimestampMixin

if TYPE_CHECKING:
    from portfolios.models import PortfolioModel


class AccountType(StrEnum):
    BROKER = "broker"
    BANK = "bank"
    CEX = "cex"


def _enum_values(enum_class: type[StrEnum]) -> list[str]:
    return [member.value for member in enum_class]


class AccountModel(TimestampMixin, Base):
    """A broker, bank, or centralized-exchange account within a portfolio."""

    __tablename__ = "accounts"
    __table_args__ = (
        CheckConstraint("btrim(name) <> ''", name="account_name_not_blank"),
        CheckConstraint(
            "institution_name IS NULL OR btrim(institution_name) <> ''",
            name="account_institution_name_not_blank",
        ),
    )

    id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid4)
    portfolio_id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("portfolios.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    account_type: Mapped[AccountType] = mapped_column(
        Enum(
            AccountType,
            name="account_type",
            native_enum=False,
            create_constraint=True,
            values_callable=_enum_values,
        ),
        nullable=False,
    )
    institution_name: Mapped[str | None] = mapped_column(String(200))

    portfolio: Mapped[PortfolioModel] = relationship(back_populates="accounts")
