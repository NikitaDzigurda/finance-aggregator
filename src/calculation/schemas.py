from __future__ import annotations

from typing import Annotated, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, StringConstraints

from calculation.contracts import CostBasisMethod
from calculation.models import CalculationStatus
from shared.exact import AwareDateTime, CurrencyCode, Money, Price, Quantity

type DiagnosticCode = Annotated[
    str,
    StringConstraints(strict=True, min_length=1, max_length=100, pattern=r"^[a-z0-9_]+$"),
]


class CalculationDiagnosticResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    severity: Literal["warning", "error"]
    code: DiagnosticCode
    message: str
    account_id: UUID | None = None
    instrument_id: UUID | None = None
    operation_id: UUID | None = None


class RecalculatePositionsRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    as_of: AwareDateTime | None = None
    cost_basis_method: CostBasisMethod = CostBasisMethod.WEIGHTED_AVERAGE


class CalculatedPositionResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True, extra="forbid")

    account_id: UUID
    instrument_id: UUID
    quantity: Quantity
    cost_currency: CurrencyCode | None
    average_cost: Price | None
    cost_basis: Money | None
    valuation_currency: CurrencyCode | None
    market_price: Price | None
    market_value: Money | None
    realised_pnl: Money | None
    unrealised_pnl: Money | None
    diagnostics: list[CalculationDiagnosticResponse]


class CalculatedCashBalanceResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True, extra="forbid")

    account_id: UUID
    currency: CurrencyCode
    amount: Money


class CalculatedCurrencyMetricsResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True, extra="forbid")

    account_id: UUID
    currency: CurrencyCode
    fees: Money
    taxes: Money
    income: Money
    realised_pnl: Money


class PositionSnapshotResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True, extra="forbid")

    portfolio_id: UUID
    cost_basis_method: CostBasisMethod
    status: CalculationStatus
    as_of: AwareDateTime
    operation_count: int = Field(ge=0)
    diagnostics: list[CalculationDiagnosticResponse]
    positions: list[CalculatedPositionResponse]
    cash_balances: list[CalculatedCashBalanceResponse]
    currency_metrics: list[CalculatedCurrencyMetricsResponse]
    created_at: AwareDateTime
    updated_at: AwareDateTime
