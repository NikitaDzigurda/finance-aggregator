from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from enum import StrEnum
from uuid import UUID

CALCULATION_CONTRACT_VERSION = 1


class CostBasisMethod(StrEnum):
    WEIGHTED_AVERAGE = "weighted_average"


class CalculationTradeSide(StrEnum):
    BUY = "buy"
    SELL = "sell"


class CalculationCashDirection(StrEnum):
    DEPOSIT = "deposit"
    WITHDRAWAL = "withdrawal"


class CalculationTransferDirection(StrEnum):
    INBOUND = "inbound"
    OUTBOUND = "outbound"


class CalculationCorporateActionType(StrEnum):
    SPLIT = "split"
    OTHER = "other"


@dataclass(frozen=True, slots=True)
class TradeEvent:
    side: CalculationTradeSide
    instrument_id: UUID
    quantity: Decimal
    price: Decimal
    price_currency: str


@dataclass(frozen=True, slots=True)
class IncomeEvent:
    amount: Decimal
    currency: str


@dataclass(frozen=True, slots=True)
class FeeEvent:
    amount: Decimal
    currency: str


@dataclass(frozen=True, slots=True)
class TaxEvent:
    amount: Decimal
    currency: str


@dataclass(frozen=True, slots=True)
class CashMovementEvent:
    direction: CalculationCashDirection
    amount: Decimal
    currency: str


@dataclass(frozen=True, slots=True)
class CurrencyExchangeEvent:
    sold_amount: Decimal
    sold_currency: str
    bought_amount: Decimal
    bought_currency: str


@dataclass(frozen=True, slots=True)
class AssetTransferEvent:
    direction: CalculationTransferDirection
    instrument_id: UUID
    quantity: Decimal


@dataclass(frozen=True, slots=True)
class CorporateActionEvent:
    action_type: CalculationCorporateActionType
    instrument_id: UUID
    quantity_change: Decimal


@dataclass(frozen=True, slots=True)
class BondRedemptionEvent:
    instrument_id: UUID
    quantity: Decimal
    amount: Decimal
    currency: str


@dataclass(frozen=True, slots=True)
class AssetAdjustmentEvent:
    instrument_id: UUID
    quantity_change: Decimal


@dataclass(frozen=True, slots=True)
class CashAdjustmentEvent:
    amount_change: Decimal
    currency: str


type CalculationEvent = (
    TradeEvent
    | IncomeEvent
    | FeeEvent
    | TaxEvent
    | CashMovementEvent
    | CurrencyExchangeEvent
    | AssetTransferEvent
    | CorporateActionEvent
    | BondRedemptionEvent
    | AssetAdjustmentEvent
    | CashAdjustmentEvent
)


@dataclass(frozen=True, slots=True)
class CalculationOperation:
    operation_id: UUID
    account_id: UUID
    occurred_at: datetime
    event: CalculationEvent


@dataclass(frozen=True, slots=True)
class CalculationPrice:
    instrument_id: UUID
    price: Decimal
    currency: str
    observed_at: datetime


@dataclass(frozen=True, slots=True)
class CalculationDiagnostic:
    severity: str
    code: str
    message: str
    account_id: UUID | None = None
    instrument_id: UUID | None = None
    operation_id: UUID | None = None


@dataclass(frozen=True, slots=True)
class CalculatedPosition:
    account_id: UUID
    instrument_id: UUID
    quantity: Decimal
    cost_currency: str | None
    average_cost: Decimal | None
    cost_basis: Decimal | None
    valuation_currency: str | None
    market_price: Decimal | None
    market_value: Decimal | None
    realised_pnl: Decimal | None
    unrealised_pnl: Decimal | None
    diagnostics: tuple[CalculationDiagnostic, ...]


@dataclass(frozen=True, slots=True)
class CalculatedCashBalance:
    account_id: UUID
    currency: str
    amount: Decimal


@dataclass(frozen=True, slots=True)
class CalculatedCurrencyMetrics:
    account_id: UUID
    currency: str
    fees: Decimal
    taxes: Decimal
    income: Decimal
    realised_pnl: Decimal


@dataclass(frozen=True, slots=True)
class CalculationInput:
    cost_basis_method: CostBasisMethod
    operations: tuple[CalculationOperation, ...]
    prices: tuple[CalculationPrice, ...]


@dataclass(frozen=True, slots=True)
class CalculationOutput:
    positions: tuple[CalculatedPosition, ...]
    cash_balances: tuple[CalculatedCashBalance, ...]
    currency_metrics: tuple[CalculatedCurrencyMetrics, ...]
    diagnostics: tuple[CalculationDiagnostic, ...]
