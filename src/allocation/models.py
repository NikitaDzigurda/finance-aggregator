from __future__ import annotations

from enum import StrEnum
from uuid import UUID, uuid4

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    Enum,
    ForeignKey,
    Index,
    String,
    Uuid,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column

from shared.database import Base
from shared.models import TimestampMixin


class AssetClass(StrEnum):
    EQUITY = "equity"
    FIXED_INCOME = "fixed_income"
    FUND = "fund"
    DERIVATIVE = "derivative"
    CRYPTO = "crypto"
    CASH = "cash"
    OTHER = "other"


def _enum_values(enum_class: type[StrEnum]) -> list[str]:
    return [member.value for member in enum_class]


class AllocationCategoryModel(TimestampMixin, Base):
    __tablename__ = "allocation_categories"
    __table_args__ = (
        CheckConstraint("btrim(name) <> ''", name="allocation_category_name_not_blank"),
        CheckConstraint(
            "(is_system AND portfolio_id IS NULL) OR (NOT is_system AND portfolio_id IS NOT NULL)",
            name="allocation_category_scope",
        ),
        Index(
            "uq_allocation_categories_system_class",
            "system_class",
            unique=True,
            postgresql_where=text("is_system"),
        ),
        Index(
            "uq_allocation_categories_portfolio_name",
            "portfolio_id",
            text("lower(name)"),
            unique=True,
            postgresql_where=text("NOT is_system"),
        ),
    )

    id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid4)
    portfolio_id: Mapped[UUID | None] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("portfolios.id", ondelete="CASCADE"),
        nullable=True,
        index=True,
    )
    name: Mapped[str] = mapped_column(String(100), nullable=False)
    system_class: Mapped[AssetClass] = mapped_column(
        Enum(
            AssetClass,
            name="allocation_asset_class",
            native_enum=False,
            create_constraint=True,
            values_callable=_enum_values,
        ),
        nullable=False,
    )
    is_system: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)


class InstrumentCategoryOverrideModel(TimestampMixin, Base):
    __tablename__ = "instrument_category_overrides"
    __table_args__ = (
        Index(
            "ix_instrument_category_overrides_category_id",
            "category_id",
        ),
    )

    portfolio_id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("portfolios.id", ondelete="CASCADE"),
        primary_key=True,
    )
    instrument_id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("instruments.id", ondelete="CASCADE"),
        primary_key=True,
    )
    category_id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey(
            "allocation_categories.id",
            deferrable=True,
            initially="DEFERRED",
        ),
        nullable=False,
    )
