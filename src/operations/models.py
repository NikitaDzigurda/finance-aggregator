from __future__ import annotations

from datetime import datetime
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
    Uuid,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from shared.database import Base
from shared.exact import TimePrecision
from shared.models import TimestampMixin


class OperationType(StrEnum):
    TRADE = "trade"
    INCOME = "income"
    FEE = "fee"
    TAX = "tax"
    CASH_MOVEMENT = "cash_movement"
    CURRENCY_EXCHANGE = "currency_exchange"
    CRYPTO_TRANSFER = "crypto_transfer"
    CORPORATE_ACTION = "corporate_action"
    BOND_REDEMPTION = "bond_redemption"
    BALANCE_ADJUSTMENT = "balance_adjustment"


class OperationSourceType(StrEnum):
    MANUAL = "manual"
    CSV_IMPORT = "csv_import"
    XLSX_IMPORT = "xlsx_import"
    PDF_IMPORT = "pdf_import"


def _enum_values(enum_class: type[StrEnum]) -> list[str]:
    return [member.value for member in enum_class]


class OperationModel(TimestampMixin, Base):
    __tablename__ = "operations"
    __table_args__ = (
        CheckConstraint(
            "jsonb_typeof(payload) = 'object'",
            name="operation_payload_object",
        ),
        CheckConstraint(
            "source_row_number IS NULL OR source_row_number > 0",
            name="operation_source_row_positive",
        ),
        CheckConstraint(
            "source_type <> 'manual' OR (import_batch_id IS NULL "
            "AND source_operation_id IS NULL AND source_row_number IS NULL)",
            name="operation_manual_source_fields",
        ),
        CheckConstraint(
            "source_type = 'manual' OR import_batch_id IS NOT NULL",
            name="operation_import_source_batch",
        ),
        CheckConstraint(
            "(source_type = 'manual' AND fingerprint IS NULL "
            "AND deduplication_key IS NULL) OR "
            "(source_type <> 'manual' AND fingerprint IS NOT NULL "
            "AND deduplication_key IS NOT NULL)",
            name="operation_source_fingerprint",
        ),
        CheckConstraint(
            "fingerprint IS NULL OR fingerprint ~ '^[0-9a-f]{64}$'",
            name="operation_fingerprint_format",
        ),
        CheckConstraint(
            "deduplication_key IS NULL OR deduplication_key ~ '^[0-9a-f]{64}$'",
            name="operation_deduplication_key_format",
        ),
        CheckConstraint(
            "correction_of_operation_id IS NULL OR correction_of_operation_id <> id",
            name="operation_not_self_correction",
        ),
        ForeignKeyConstraint(
            ["account_id", "portfolio_id"],
            ["accounts.id", "accounts.portfolio_id"],
            name="fk_operations_account_portfolio_accounts",
            ondelete="RESTRICT",
        ),
        Index("ix_operations_portfolio_occurred_at", "portfolio_id", "occurred_at"),
        Index("ix_operations_account_occurred_at", "account_id", "occurred_at"),
        Index("ix_operations_type_occurred_at", "operation_type", "occurred_at"),
        Index("ix_operations_instrument_occurred_at", "instrument_id", "occurred_at"),
        Index("ix_operations_account_fingerprint", "account_id", "fingerprint"),
        Index(
            "uq_operations_account_deduplication_key",
            "account_id",
            "deduplication_key",
            unique=True,
            postgresql_where=text("deduplication_key IS NOT NULL"),
        ),
    )

    id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid4)
    portfolio_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), nullable=False)
    account_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), nullable=False)
    operation_type: Mapped[OperationType] = mapped_column(
        Enum(
            OperationType,
            name="operation_type",
            native_enum=False,
            create_constraint=True,
            values_callable=_enum_values,
        ),
        nullable=False,
    )
    occurred_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    time_precision: Mapped[TimePrecision] = mapped_column(
        Enum(
            TimePrecision,
            name="operation_time_precision",
            native_enum=False,
            create_constraint=True,
            values_callable=_enum_values,
        ),
        nullable=False,
    )
    source_type: Mapped[OperationSourceType] = mapped_column(
        Enum(
            OperationSourceType,
            name="operation_source_type",
            native_enum=False,
            create_constraint=True,
            values_callable=_enum_values,
        ),
        nullable=False,
    )
    import_batch_id: Mapped[UUID | None] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("import_batches.id", ondelete="RESTRICT"),
    )
    source_operation_id: Mapped[str | None] = mapped_column(String(256))
    source_row_number: Mapped[int | None] = mapped_column(Integer)
    fingerprint: Mapped[str | None] = mapped_column(String(64))
    deduplication_key: Mapped[str | None] = mapped_column(String(64))
    payload: Mapped[dict[str, object]] = mapped_column(JSONB, nullable=False)
    instrument_id: Mapped[UUID | None] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("instruments.id", ondelete="RESTRICT"),
    )
    correction_of_operation_id: Mapped[UUID | None] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("operations.id", ondelete="RESTRICT"),
    )
    note: Mapped[str | None] = mapped_column(String(1000))
