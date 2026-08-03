from __future__ import annotations

from decimal import Decimal
from enum import StrEnum
from typing import Annotated, Literal, Self, cast
from uuid import UUID

from pydantic import (
    AfterValidator,
    BaseModel,
    ConfigDict,
    Field,
    StringConstraints,
    model_validator,
)
from pydantic_core import PydanticCustomError

from operations.models import OperationModel, OperationSourceType, OperationType
from shared.exact import (
    AwareDateTime,
    CurrencyCode,
    Money,
    OperationTimestamp,
    Price,
    Quantity,
    TimePrecision,
)


def _positive(value: Decimal) -> Decimal:
    if value <= 0:
        raise PydanticCustomError("value_not_positive", "Value must be greater than zero")
    return value


def _non_zero(value: Decimal) -> Decimal:
    if value == 0:
        raise PydanticCustomError("value_zero", "Value must not be zero")
    return value


type PositiveMoney = Annotated[Money, AfterValidator(_positive)]
type PositiveQuantity = Annotated[Quantity, AfterValidator(_positive)]
type NonZeroMoney = Annotated[Money, AfterValidator(_non_zero)]
type NonZeroQuantity = Annotated[Quantity, AfterValidator(_non_zero)]
type PositivePrice = Annotated[Price, AfterValidator(_positive)]
type OperationNote = Annotated[
    str,
    StringConstraints(strict=True, strip_whitespace=True, min_length=1, max_length=1000),
]


class TradeSide(StrEnum):
    BUY = "buy"
    SELL = "sell"


class IncomeType(StrEnum):
    DIVIDEND = "dividend"
    COUPON = "coupon"
    INTEREST = "interest"
    OTHER = "other"


class CashMovementDirection(StrEnum):
    DEPOSIT = "deposit"
    WITHDRAWAL = "withdrawal"


class TransferDirection(StrEnum):
    INBOUND = "inbound"
    OUTBOUND = "outbound"


class CorporateActionType(StrEnum):
    SPLIT = "split"
    MERGER = "merger"
    SPIN_OFF = "spin_off"
    SYMBOL_CHANGE = "symbol_change"
    OTHER = "other"


class PayloadBase(BaseModel):
    model_config = ConfigDict(extra="forbid")


class TradePayload(PayloadBase):
    side: TradeSide
    instrument_id: UUID
    quantity: PositiveQuantity
    price: PositivePrice
    price_currency: CurrencyCode


class IncomePayload(PayloadBase):
    income_type: IncomeType
    amount: PositiveMoney
    currency: CurrencyCode
    instrument_id: UUID | None = None


class FeePayload(PayloadBase):
    amount: PositiveMoney
    currency: CurrencyCode
    instrument_id: UUID | None = None


class TaxPayload(PayloadBase):
    amount: PositiveMoney
    currency: CurrencyCode
    instrument_id: UUID | None = None


class CashMovementPayload(PayloadBase):
    direction: CashMovementDirection
    amount: PositiveMoney
    currency: CurrencyCode


class CurrencyExchangePayload(PayloadBase):
    sold_amount: PositiveMoney
    sold_currency: CurrencyCode
    bought_amount: PositiveMoney
    bought_currency: CurrencyCode

    @model_validator(mode="after")
    def validate_currencies(self) -> Self:
        if self.sold_currency == self.bought_currency:
            raise PydanticCustomError(
                "currency_exchange_same_currency",
                "Sold and bought currencies must differ",
            )
        return self


class CryptoTransferPayload(PayloadBase):
    direction: TransferDirection
    instrument_id: UUID
    quantity: PositiveQuantity


class CorporateActionPayload(PayloadBase):
    action_type: CorporateActionType
    instrument_id: UUID
    quantity_change: NonZeroQuantity
    description: OperationNote


class BondRedemptionPayload(PayloadBase):
    instrument_id: UUID
    quantity: PositiveQuantity
    amount: PositiveMoney
    currency: CurrencyCode


class BalanceAdjustmentPayload(PayloadBase):
    instrument_id: UUID | None = None
    quantity_change: NonZeroQuantity | None = None
    amount_change: NonZeroMoney | None = None
    currency: CurrencyCode | None = None
    reason: OperationNote

    @model_validator(mode="after")
    def validate_adjustment_shape(self) -> Self:
        asset_adjustment = self.instrument_id is not None or self.quantity_change is not None
        cash_adjustment = self.amount_change is not None or self.currency is not None
        if asset_adjustment == cash_adjustment:
            raise PydanticCustomError(
                "balance_adjustment_shape",
                "Specify exactly one complete asset or cash adjustment",
            )
        if asset_adjustment and (self.instrument_id is None or self.quantity_change is None):
            raise PydanticCustomError(
                "balance_adjustment_shape",
                "Asset adjustment requires instrument_id and quantity_change",
            )
        if cash_adjustment and (self.amount_change is None or self.currency is None):
            raise PydanticCustomError(
                "balance_adjustment_shape",
                "Cash adjustment requires amount_change and currency",
            )
        return self


type OperationPayload = (
    TradePayload
    | IncomePayload
    | FeePayload
    | TaxPayload
    | CashMovementPayload
    | CurrencyExchangePayload
    | CryptoTransferPayload
    | CorporateActionPayload
    | BondRedemptionPayload
    | BalanceAdjustmentPayload
)


class OperationCreateBase(BaseModel):
    model_config = ConfigDict(extra="forbid")

    portfolio_id: UUID
    account_id: UUID
    occurred_at: AwareDateTime
    time_precision: TimePrecision
    correction_of_operation_id: UUID | None = None
    note: OperationNote | None = None

    @model_validator(mode="after")
    def validate_time_precision(self) -> Self:
        OperationTimestamp(occurred_at=self.occurred_at, precision=self.time_precision)
        if self.correction_of_operation_id is not None and self.note is None:
            raise PydanticCustomError(
                "correction_note_required",
                "Correction operations require a note",
            )
        return self


class TradeOperationCreate(OperationCreateBase):
    operation_type: Literal[OperationType.TRADE]
    payload: TradePayload


class IncomeOperationCreate(OperationCreateBase):
    operation_type: Literal[OperationType.INCOME]
    payload: IncomePayload


class FeeOperationCreate(OperationCreateBase):
    operation_type: Literal[OperationType.FEE]
    payload: FeePayload


class TaxOperationCreate(OperationCreateBase):
    operation_type: Literal[OperationType.TAX]
    payload: TaxPayload


class CashMovementOperationCreate(OperationCreateBase):
    operation_type: Literal[OperationType.CASH_MOVEMENT]
    payload: CashMovementPayload


class CurrencyExchangeOperationCreate(OperationCreateBase):
    operation_type: Literal[OperationType.CURRENCY_EXCHANGE]
    payload: CurrencyExchangePayload


class CryptoTransferOperationCreate(OperationCreateBase):
    operation_type: Literal[OperationType.CRYPTO_TRANSFER]
    payload: CryptoTransferPayload


class CorporateActionOperationCreate(OperationCreateBase):
    operation_type: Literal[OperationType.CORPORATE_ACTION]
    payload: CorporateActionPayload


class BondRedemptionOperationCreate(OperationCreateBase):
    operation_type: Literal[OperationType.BOND_REDEMPTION]
    payload: BondRedemptionPayload


class BalanceAdjustmentOperationCreate(OperationCreateBase):
    operation_type: Literal[OperationType.BALANCE_ADJUSTMENT]
    payload: BalanceAdjustmentPayload


type OperationCreate = Annotated[
    TradeOperationCreate
    | IncomeOperationCreate
    | FeeOperationCreate
    | TaxOperationCreate
    | CashMovementOperationCreate
    | CurrencyExchangeOperationCreate
    | CryptoTransferOperationCreate
    | CorporateActionOperationCreate
    | BondRedemptionOperationCreate
    | BalanceAdjustmentOperationCreate,
    Field(discriminator="operation_type"),
]


class OperationSourceResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    type: OperationSourceType
    import_batch_id: UUID | None = None
    source_operation_id: str | None = None
    row_number: int | None = None


class OperationResponseBase(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: UUID
    portfolio_id: UUID
    account_id: UUID
    occurred_at: AwareDateTime
    time_precision: TimePrecision
    source: OperationSourceResponse
    correction_of_operation_id: UUID | None
    note: str | None
    created_at: AwareDateTime
    updated_at: AwareDateTime


class TradeOperationResponse(OperationResponseBase):
    operation_type: Literal[OperationType.TRADE]
    payload: TradePayload


class IncomeOperationResponse(OperationResponseBase):
    operation_type: Literal[OperationType.INCOME]
    payload: IncomePayload


class FeeOperationResponse(OperationResponseBase):
    operation_type: Literal[OperationType.FEE]
    payload: FeePayload


class TaxOperationResponse(OperationResponseBase):
    operation_type: Literal[OperationType.TAX]
    payload: TaxPayload


class CashMovementOperationResponse(OperationResponseBase):
    operation_type: Literal[OperationType.CASH_MOVEMENT]
    payload: CashMovementPayload


class CurrencyExchangeOperationResponse(OperationResponseBase):
    operation_type: Literal[OperationType.CURRENCY_EXCHANGE]
    payload: CurrencyExchangePayload


class CryptoTransferOperationResponse(OperationResponseBase):
    operation_type: Literal[OperationType.CRYPTO_TRANSFER]
    payload: CryptoTransferPayload


class CorporateActionOperationResponse(OperationResponseBase):
    operation_type: Literal[OperationType.CORPORATE_ACTION]
    payload: CorporateActionPayload


class BondRedemptionOperationResponse(OperationResponseBase):
    operation_type: Literal[OperationType.BOND_REDEMPTION]
    payload: BondRedemptionPayload


class BalanceAdjustmentOperationResponse(OperationResponseBase):
    operation_type: Literal[OperationType.BALANCE_ADJUSTMENT]
    payload: BalanceAdjustmentPayload


type OperationResponse = Annotated[
    TradeOperationResponse
    | IncomeOperationResponse
    | FeeOperationResponse
    | TaxOperationResponse
    | CashMovementOperationResponse
    | CurrencyExchangeOperationResponse
    | CryptoTransferOperationResponse
    | CorporateActionOperationResponse
    | BondRedemptionOperationResponse
    | BalanceAdjustmentOperationResponse,
    Field(discriminator="operation_type"),
]


_RESPONSE_MODELS: dict[OperationType, type[OperationResponseBase]] = {
    OperationType.TRADE: TradeOperationResponse,
    OperationType.INCOME: IncomeOperationResponse,
    OperationType.FEE: FeeOperationResponse,
    OperationType.TAX: TaxOperationResponse,
    OperationType.CASH_MOVEMENT: CashMovementOperationResponse,
    OperationType.CURRENCY_EXCHANGE: CurrencyExchangeOperationResponse,
    OperationType.CRYPTO_TRANSFER: CryptoTransferOperationResponse,
    OperationType.CORPORATE_ACTION: CorporateActionOperationResponse,
    OperationType.BOND_REDEMPTION: BondRedemptionOperationResponse,
    OperationType.BALANCE_ADJUSTMENT: BalanceAdjustmentOperationResponse,
}


def operation_response(record: OperationModel) -> OperationResponse:
    operation_type = OperationType(record.operation_type)
    response_model = _RESPONSE_MODELS[operation_type]
    data = {
        "id": record.id,
        "portfolio_id": record.portfolio_id,
        "account_id": record.account_id,
        "operation_type": operation_type,
        "occurred_at": record.occurred_at,
        "time_precision": record.time_precision,
        "source": {
            "type": record.source_type,
            "import_batch_id": record.import_batch_id,
            "source_operation_id": record.source_operation_id,
            "row_number": record.source_row_number,
        },
        "payload": record.payload,
        "correction_of_operation_id": record.correction_of_operation_id,
        "note": record.note,
        "created_at": record.created_at,
        "updated_at": record.updated_at,
    }
    return cast(OperationResponse, response_model.model_validate(data))


class OperationListResponse(BaseModel):
    items: list[OperationResponse]
    limit: int
    offset: int
