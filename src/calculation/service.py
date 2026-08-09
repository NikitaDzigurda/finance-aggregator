from __future__ import annotations

from datetime import UTC, datetime
from decimal import DecimalException
from uuid import UUID

from fastapi import status
from sqlalchemy import Select, delete, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from calculation.contracts import (
    AssetAdjustmentEvent,
    AssetTransferEvent,
    BondRedemptionEvent,
    CalculationCashDirection,
    CalculationCorporateActionType,
    CalculationDiagnostic,
    CalculationEvent,
    CalculationInput,
    CalculationOperation,
    CalculationPrice,
    CalculationTradeSide,
    CalculationTransferDirection,
    CashAdjustmentEvent,
    CashMovementEvent,
    CorporateActionEvent,
    CostBasisMethod,
    CurrencyExchangeEvent,
    FeeEvent,
    IncomeEvent,
    TaxEvent,
    TradeEvent,
)
from calculation.engine import calculate_positions
from calculation.models import (
    CalculatedCashBalanceModel,
    CalculatedCurrencyMetricsModel,
    CalculatedPositionModel,
    CalculationSnapshotModel,
    CalculationStatus,
)
from calculation.schemas import (
    CalculatedCashBalanceResponse,
    CalculatedCurrencyMetricsResponse,
    CalculatedPositionResponse,
    CalculationDiagnosticResponse,
    PositionSnapshotResponse,
)
from operations.models import OperationModel
from operations.schemas import (
    BalanceAdjustmentPayload,
    BondRedemptionPayload,
    CashMovementPayload,
    CorporateActionPayload,
    CorporateActionType,
    CryptoTransferPayload,
    CurrencyExchangePayload,
    FeePayload,
    IncomePayload,
    OperationPayload,
    TaxPayload,
    TradePayload,
    operation_response,
)
from portfolios.models import PortfolioModel
from pricing.models import MarketPriceModel
from shared.errors import ApiErrorException
from shared.exact import ExactDecimalError


async def recalculate_portfolio(
    session: AsyncSession,
    *,
    portfolio_id: UUID,
    as_of: datetime | None,
    cost_basis_method: CostBasisMethod,
) -> CalculationSnapshotModel:
    portfolio = await session.scalar(
        select(PortfolioModel)
        .where(PortfolioModel.id == portfolio_id)
        .with_for_update()
    )
    if portfolio is None:
        _not_found()
    calculation_time = as_of or datetime.now(UTC)
    operation_records = list(
        await session.scalars(
            select(OperationModel)
            .where(
                OperationModel.portfolio_id == portfolio_id,
                OperationModel.occurred_at <= calculation_time,
            )
            .order_by(OperationModel.occurred_at, OperationModel.id)
        )
    )
    price_records = list(
        await session.scalars(
            select(MarketPriceModel)
            .where(MarketPriceModel.observed_at <= calculation_time)
            .order_by(MarketPriceModel.observed_at, MarketPriceModel.id)
        )
    )
    request = CalculationInput(
        cost_basis_method=cost_basis_method,
        operations=tuple(_calculation_operation(item) for item in operation_records),
        prices=tuple(
            CalculationPrice(
                instrument_id=item.instrument_id,
                price=item.price,
                currency=item.currency,
                observed_at=item.observed_at,
            )
            for item in price_records
        ),
    )
    try:
        output = calculate_positions(request)
    except (DecimalException, ExactDecimalError, ValueError) as exc:
        raise ApiErrorException(
            status_code=status.HTTP_409_CONFLICT,
            code="calculation_failed",
            message="Portfolio calculation could not produce exact results",
        ) from exc

    snapshot = await session.get(CalculationSnapshotModel, portfolio_id)
    if snapshot is None:
        snapshot = CalculationSnapshotModel(
            portfolio_id=portfolio_id,
            cost_basis_method=cost_basis_method,
            status=CalculationStatus.COMPLETED,
            as_of=calculation_time,
            operation_count=len(operation_records),
            diagnostics=[],
        )
        session.add(snapshot)
        await session.flush()
    else:
        await session.execute(
            delete(CalculatedPositionModel).where(
                CalculatedPositionModel.portfolio_id == portfolio_id
            )
        )
        await session.execute(
            delete(CalculatedCashBalanceModel).where(
                CalculatedCashBalanceModel.portfolio_id == portfolio_id
            )
        )
        await session.execute(
            delete(CalculatedCurrencyMetricsModel).where(
                CalculatedCurrencyMetricsModel.portfolio_id == portfolio_id
            )
        )
        await session.flush()

    snapshot.cost_basis_method = cost_basis_method
    snapshot.status = (
        CalculationStatus.COMPLETED_WITH_DIAGNOSTICS
        if output.diagnostics
        else CalculationStatus.COMPLETED
    )
    snapshot.as_of = calculation_time
    snapshot.operation_count = len(operation_records)
    snapshot.diagnostics = [_diagnostic_data(item) for item in output.diagnostics]
    session.add_all(
        [
            CalculatedPositionModel(
                portfolio_id=portfolio_id,
                account_id=item.account_id,
                instrument_id=item.instrument_id,
                quantity=item.quantity,
                cost_currency=item.cost_currency,
                average_cost=item.average_cost,
                cost_basis=item.cost_basis,
                valuation_currency=item.valuation_currency,
                market_price=item.market_price,
                market_value=item.market_value,
                realised_pnl=item.realised_pnl,
                unrealised_pnl=item.unrealised_pnl,
                diagnostics=[_diagnostic_data(value) for value in item.diagnostics],
            )
            for item in output.positions
        ]
    )
    session.add_all(
        [
            CalculatedCashBalanceModel(
                portfolio_id=portfolio_id,
                account_id=item.account_id,
                currency=item.currency,
                amount=item.amount,
            )
            for item in output.cash_balances
        ]
    )
    session.add_all(
        [
            CalculatedCurrencyMetricsModel(
                portfolio_id=portfolio_id,
                account_id=item.account_id,
                currency=item.currency,
                fees=item.fees,
                taxes=item.taxes,
                income=item.income,
                realised_pnl=item.realised_pnl,
            )
            for item in output.currency_metrics
        ]
    )
    try:
        await session.commit()
    except IntegrityError as exc:
        await session.rollback()
        raise ApiErrorException(
            status_code=status.HTTP_409_CONFLICT,
            code="calculation_conflict",
            message="Portfolio calculation conflicts with current ledger data",
        ) from exc
    stored = await get_position_snapshot(session, portfolio_id)
    if stored is None:
        raise RuntimeError("Stored calculation snapshot was not found")
    return stored


async def get_position_snapshot(
    session: AsyncSession,
    portfolio_id: UUID,
) -> CalculationSnapshotModel | None:
    statement: Select[tuple[CalculationSnapshotModel]] = (
        select(CalculationSnapshotModel)
        .where(CalculationSnapshotModel.portfolio_id == portfolio_id)
        .options(
            selectinload(CalculationSnapshotModel.positions),
            selectinload(CalculationSnapshotModel.cash_balances),
            selectinload(CalculationSnapshotModel.currency_metrics),
        )
    )
    result = await session.scalars(statement)
    return result.one_or_none()


def position_snapshot_response(
    snapshot: CalculationSnapshotModel,
) -> PositionSnapshotResponse:
    return PositionSnapshotResponse(
        portfolio_id=snapshot.portfolio_id,
        cost_basis_method=snapshot.cost_basis_method,
        status=snapshot.status,
        as_of=snapshot.as_of,
        operation_count=snapshot.operation_count,
        diagnostics=[
            CalculationDiagnosticResponse.model_validate(item)
            for item in snapshot.diagnostics
        ],
        positions=[
            CalculatedPositionResponse.model_validate(item)
            for item in sorted(
                snapshot.positions,
                key=lambda value: (str(value.account_id), str(value.instrument_id)),
            )
        ],
        cash_balances=[
            CalculatedCashBalanceResponse.model_validate(item)
            for item in sorted(
                snapshot.cash_balances,
                key=lambda value: (str(value.account_id), value.currency),
            )
        ],
        currency_metrics=[
            CalculatedCurrencyMetricsResponse.model_validate(item)
            for item in sorted(
                snapshot.currency_metrics,
                key=lambda value: (str(value.account_id), value.currency),
            )
        ],
        created_at=snapshot.created_at,
        updated_at=snapshot.updated_at,
    )


def _calculation_operation(record: OperationModel) -> CalculationOperation:
    response = operation_response(record)
    return CalculationOperation(
        operation_id=response.id,
        account_id=response.account_id,
        occurred_at=response.occurred_at,
        event=_calculation_event(response.payload),
    )


def _calculation_event(payload: OperationPayload) -> CalculationEvent:
    if isinstance(payload, TradePayload):
        return TradeEvent(
            side=CalculationTradeSide(payload.side.value),
            instrument_id=payload.instrument_id,
            quantity=payload.quantity,
            price=payload.price,
            price_currency=payload.price_currency,
        )
    if isinstance(payload, IncomePayload):
        return IncomeEvent(amount=payload.amount, currency=payload.currency)
    if isinstance(payload, FeePayload):
        return FeeEvent(amount=payload.amount, currency=payload.currency)
    if isinstance(payload, TaxPayload):
        return TaxEvent(amount=payload.amount, currency=payload.currency)
    if isinstance(payload, CashMovementPayload):
        return CashMovementEvent(
            direction=CalculationCashDirection(payload.direction.value),
            amount=payload.amount,
            currency=payload.currency,
        )
    if isinstance(payload, CurrencyExchangePayload):
        return CurrencyExchangeEvent(
            sold_amount=payload.sold_amount,
            sold_currency=payload.sold_currency,
            bought_amount=payload.bought_amount,
            bought_currency=payload.bought_currency,
        )
    if isinstance(payload, CryptoTransferPayload):
        return AssetTransferEvent(
            direction=CalculationTransferDirection(payload.direction.value),
            instrument_id=payload.instrument_id,
            quantity=payload.quantity,
        )
    if isinstance(payload, CorporateActionPayload):
        action_type = (
            CalculationCorporateActionType.SPLIT
            if payload.action_type is CorporateActionType.SPLIT
            else CalculationCorporateActionType.OTHER
        )
        return CorporateActionEvent(
            action_type=action_type,
            instrument_id=payload.instrument_id,
            quantity_change=payload.quantity_change,
        )
    if isinstance(payload, BondRedemptionPayload):
        return BondRedemptionEvent(
            instrument_id=payload.instrument_id,
            quantity=payload.quantity,
            amount=payload.amount,
            currency=payload.currency,
        )
    if isinstance(payload, BalanceAdjustmentPayload):
        if payload.instrument_id is not None and payload.quantity_change is not None:
            return AssetAdjustmentEvent(
                instrument_id=payload.instrument_id,
                quantity_change=payload.quantity_change,
            )
        if payload.amount_change is not None and payload.currency is not None:
            return CashAdjustmentEvent(
                amount_change=payload.amount_change,
                currency=payload.currency,
            )
    raise AssertionError("Unsupported operation payload")


def _diagnostic_data(value: CalculationDiagnostic) -> dict[str, object]:
    result: dict[str, object] = {
        "severity": value.severity,
        "code": value.code,
        "message": value.message,
    }
    if value.account_id is not None:
        result["account_id"] = str(value.account_id)
    if value.instrument_id is not None:
        result["instrument_id"] = str(value.instrument_id)
    if value.operation_id is not None:
        result["operation_id"] = str(value.operation_id)
    return result


def _not_found() -> None:
    raise ApiErrorException(
        status_code=status.HTTP_404_NOT_FOUND,
        code="portfolio_not_found",
        message="Portfolio was not found",
    )
