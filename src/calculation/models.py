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
    ForeignKeyConstraint,
    Index,
    Integer,
    String,
    UniqueConstraint,
    Uuid,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from calculation.contracts import CostBasisMethod
from shared.database import Base
from shared.db_types import MoneyNumeric, PriceNumeric, QuantityNumeric
from shared.models import TimestampMixin


class CalculationStatus(StrEnum):
    COMPLETED = "completed"
    COMPLETED_WITH_DIAGNOSTICS = "completed_with_diagnostics"


def _enum_values(enum_class: type[StrEnum]) -> list[str]:
    return [member.value for member in enum_class]


class CalculationSnapshotModel(TimestampMixin, Base):
    __tablename__ = "calculation_snapshots"
    __table_args__ = (
        CheckConstraint("operation_count >= 0", name="calculation_operation_count_nonnegative"),
        CheckConstraint(
            "jsonb_typeof(diagnostics) = 'array'",
            name="calculation_diagnostics_array",
        ),
    )

    portfolio_id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("portfolios.id", ondelete="CASCADE"),
        primary_key=True,
    )
    cost_basis_method: Mapped[CostBasisMethod] = mapped_column(
        Enum(
            CostBasisMethod,
            name="cost_basis_method",
            native_enum=False,
            create_constraint=True,
            values_callable=_enum_values,
        ),
        nullable=False,
    )
    status: Mapped[CalculationStatus] = mapped_column(
        Enum(
            CalculationStatus,
            name="calculation_status",
            native_enum=False,
            create_constraint=True,
            values_callable=_enum_values,
        ),
        nullable=False,
    )
    as_of: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    operation_count: Mapped[int] = mapped_column(Integer, nullable=False)
    diagnostics: Mapped[list[dict[str, object]]] = mapped_column(
        JSONB,
        nullable=False,
        default=list,
        server_default=text("'[]'::jsonb"),
    )

    positions: Mapped[list[CalculatedPositionModel]] = relationship(
        back_populates="snapshot",
        cascade="all, delete-orphan",
        passive_deletes=True,
        lazy="selectin",
    )
    cash_balances: Mapped[list[CalculatedCashBalanceModel]] = relationship(
        back_populates="snapshot",
        cascade="all, delete-orphan",
        passive_deletes=True,
        lazy="selectin",
    )
    currency_metrics: Mapped[list[CalculatedCurrencyMetricsModel]] = relationship(
        back_populates="snapshot",
        cascade="all, delete-orphan",
        passive_deletes=True,
        lazy="selectin",
    )


class CalculatedPositionModel(Base):
    __tablename__ = "calculated_positions"
    __table_args__ = (
        CheckConstraint(
            "cost_currency IS NULL OR cost_currency ~ '^[A-Z]{3}$'",
            name="calculated_position_cost_currency_format",
        ),
        CheckConstraint(
            "valuation_currency IS NULL OR valuation_currency ~ '^[A-Z]{3}$'",
            name="calculated_position_valuation_currency_format",
        ),
        CheckConstraint(
            "jsonb_typeof(diagnostics) = 'array'",
            name="calculated_position_diagnostics_array",
        ),
        ForeignKeyConstraint(
            ["account_id", "portfolio_id"],
            ["accounts.id", "accounts.portfolio_id"],
            name="fk_calculated_positions_account_portfolio_accounts",
            ondelete="CASCADE",
        ),
        UniqueConstraint(
            "portfolio_id",
            "account_id",
            "instrument_id",
            name="uq_calculated_positions_portfolio_account_instrument",
        ),
        Index("ix_calculated_positions_portfolio_id", "portfolio_id"),
    )

    id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid4)
    portfolio_id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("calculation_snapshots.portfolio_id", ondelete="CASCADE"),
        nullable=False,
    )
    account_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), nullable=False)
    instrument_id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("instruments.id", ondelete="RESTRICT"),
        nullable=False,
    )
    quantity: Mapped[Decimal] = mapped_column(QuantityNumeric(), nullable=False)
    cost_currency: Mapped[str | None] = mapped_column(String(3))
    average_cost: Mapped[Decimal | None] = mapped_column(PriceNumeric())
    cost_basis: Mapped[Decimal | None] = mapped_column(MoneyNumeric())
    valuation_currency: Mapped[str | None] = mapped_column(String(3))
    market_price: Mapped[Decimal | None] = mapped_column(PriceNumeric())
    market_value: Mapped[Decimal | None] = mapped_column(MoneyNumeric())
    realised_pnl: Mapped[Decimal | None] = mapped_column(MoneyNumeric())
    unrealised_pnl: Mapped[Decimal | None] = mapped_column(MoneyNumeric())
    diagnostics: Mapped[list[dict[str, object]]] = mapped_column(
        JSONB,
        nullable=False,
        default=list,
        server_default=text("'[]'::jsonb"),
    )

    snapshot: Mapped[CalculationSnapshotModel] = relationship(back_populates="positions")


class CalculatedCashBalanceModel(Base):
    __tablename__ = "calculated_cash_balances"
    __table_args__ = (
        CheckConstraint(
            "currency ~ '^[A-Z]{3}$'",
            name="calculated_cash_currency_format",
        ),
        ForeignKeyConstraint(
            ["account_id", "portfolio_id"],
            ["accounts.id", "accounts.portfolio_id"],
            name="fk_calculated_cash_account_portfolio_accounts",
            ondelete="CASCADE",
        ),
        UniqueConstraint(
            "portfolio_id",
            "account_id",
            "currency",
            name="uq_calculated_cash_portfolio_account_currency",
        ),
        Index("ix_calculated_cash_balances_portfolio_id", "portfolio_id"),
    )

    id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid4)
    portfolio_id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("calculation_snapshots.portfolio_id", ondelete="CASCADE"),
        nullable=False,
    )
    account_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), nullable=False)
    currency: Mapped[str] = mapped_column(String(3), nullable=False)
    amount: Mapped[Decimal] = mapped_column(MoneyNumeric(), nullable=False)

    snapshot: Mapped[CalculationSnapshotModel] = relationship(back_populates="cash_balances")


class CalculatedCurrencyMetricsModel(Base):
    __tablename__ = "calculated_currency_metrics"
    __table_args__ = (
        CheckConstraint(
            "currency ~ '^[A-Z]{3}$'",
            name="calculated_metrics_currency_format",
        ),
        CheckConstraint("fees >= 0", name="calculated_metrics_fees_nonnegative"),
        CheckConstraint("taxes >= 0", name="calculated_metrics_taxes_nonnegative"),
        CheckConstraint("income >= 0", name="calculated_metrics_income_nonnegative"),
        ForeignKeyConstraint(
            ["account_id", "portfolio_id"],
            ["accounts.id", "accounts.portfolio_id"],
            name="fk_calculated_metrics_account_portfolio_accounts",
            ondelete="CASCADE",
        ),
        UniqueConstraint(
            "portfolio_id",
            "account_id",
            "currency",
            name="uq_calculated_metrics_portfolio_account_currency",
        ),
        Index("ix_calculated_currency_metrics_portfolio_id", "portfolio_id"),
    )

    id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid4)
    portfolio_id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey(
            "calculation_snapshots.portfolio_id",
            name="fk_calc_metrics_snapshot",
            ondelete="CASCADE",
        ),
        nullable=False,
    )
    account_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), nullable=False)
    currency: Mapped[str] = mapped_column(String(3), nullable=False)
    fees: Mapped[Decimal] = mapped_column(MoneyNumeric(), nullable=False)
    taxes: Mapped[Decimal] = mapped_column(MoneyNumeric(), nullable=False)
    income: Mapped[Decimal] = mapped_column(MoneyNumeric(), nullable=False)
    realised_pnl: Mapped[Decimal] = mapped_column(MoneyNumeric(), nullable=False)

    snapshot: Mapped[CalculationSnapshotModel] = relationship(
        back_populates="currency_metrics"
    )
