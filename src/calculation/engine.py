from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field
from decimal import ROUND_HALF_EVEN, Context, Decimal, localcontext
from uuid import UUID

from calculation.contracts import (
    AssetAdjustmentEvent,
    AssetFeeEvent,
    AssetTransferEvent,
    BondRedemptionEvent,
    CalculatedCashBalance,
    CalculatedCurrencyMetrics,
    CalculatedPosition,
    CalculationBasisEffect,
    CalculationCashDirection,
    CalculationCorporateActionType,
    CalculationDiagnostic,
    CalculationInput,
    CalculationOperation,
    CalculationOperationEffect,
    CalculationOperationEffectsOutput,
    CalculationOutput,
    CalculationPrice,
    CalculationRealisedPnlEffect,
    CalculationTradeSide,
    CalculationTransferDirection,
    CashAdjustmentEvent,
    CashMovementEvent,
    CorporateActionEvent,
    CostBasisMethod,
    CryptoTradeEvent,
    CurrencyExchangeEvent,
    FeeEvent,
    IncomeEvent,
    RealisedPnlEffectStatus,
    TaxEvent,
    TradeEvent,
)
from shared.exact import MONEY_SPEC, PRICE_SPEC, QUANTITY_SPEC, validate_decimal

_CALCULATION_CONTEXT = Context(prec=100, rounding=ROUND_HALF_EVEN)
_MONEY_QUANTUM = Decimal("0.000000000000000001")
_PRICE_QUANTUM = Decimal("0.000000000000000001")
_ZERO = Decimal(0)


@dataclass(slots=True)
class _PositionState:
    account_id: UUID
    instrument_id: UUID
    quantity: Decimal = _ZERO
    cost_currency: str | None = None
    cost_basis: Decimal | None = _ZERO
    realised_pnl: Decimal | None = _ZERO
    diagnostics: list[CalculationDiagnostic] = field(default_factory=list)


@dataclass(slots=True)
class _MetricsState:
    fees: Decimal = _ZERO
    taxes: Decimal = _ZERO
    income: Decimal = _ZERO
    realised_pnl: Decimal = _ZERO


def calculate_positions(request: CalculationInput) -> CalculationOutput:
    if request.cost_basis_method is not CostBasisMethod.WEIGHTED_AVERAGE:
        raise ValueError("Unsupported cost basis method")

    positions: dict[tuple[UUID, UUID], _PositionState] = {}
    cash: defaultdict[tuple[UUID, str], Decimal] = defaultdict(Decimal)
    metrics: defaultdict[tuple[UUID, str], _MetricsState] = defaultdict(_MetricsState)
    diagnostics: list[CalculationDiagnostic] = []

    with localcontext(_CALCULATION_CONTEXT):
        for operation in sorted(
            request.operations,
            key=lambda item: (
                item.occurred_at,
                item.execution_operation_id or item.operation_id,
                item.execution_operation_id is not None,
                item.operation_id,
            ),
        ):
            _apply_operation(operation, positions, cash, metrics, diagnostics)

        latest_prices = _latest_prices(request.prices)
        position_results = tuple(
            _position_result(state, latest_prices, diagnostics)
            for state in sorted(
                positions.values(),
                key=lambda item: (str(item.account_id), str(item.instrument_id)),
            )
        )
        cash_results = tuple(
            CalculatedCashBalance(
                account_id=account_id,
                currency=currency,
                amount=_money(amount),
            )
            for (account_id, currency), amount in sorted(
                cash.items(),
                key=lambda item: (str(item[0][0]), item[0][1]),
            )
            if amount != 0
        )
        metric_results = tuple(
            CalculatedCurrencyMetrics(
                account_id=account_id,
                currency=currency,
                fees=_money(value.fees),
                taxes=_money(value.taxes),
                income=_money(value.income),
                realised_pnl=_money(value.realised_pnl),
            )
            for (account_id, currency), value in sorted(
                metrics.items(),
                key=lambda item: (str(item[0][0]), item[0][1]),
            )
        )

    return CalculationOutput(
        positions=position_results,
        cash_balances=cash_results,
        currency_metrics=metric_results,
        diagnostics=tuple(diagnostics),
    )


def calculate_operation_effects(
    request: CalculationInput,
) -> CalculationOperationEffectsOutput:
    """Replay Ledger deterministically and expose traceable per-operation effects."""
    if request.cost_basis_method is not CostBasisMethod.WEIGHTED_AVERAGE:
        raise ValueError("Unsupported cost basis method")

    positions: dict[tuple[UUID, UUID], _PositionState] = {}
    cash: defaultdict[tuple[UUID, str], Decimal] = defaultdict(Decimal)
    metrics: defaultdict[tuple[UUID, str], _MetricsState] = defaultdict(_MetricsState)
    diagnostics: list[CalculationDiagnostic] = []
    effects: list[CalculationOperationEffect] = []

    with localcontext(_CALCULATION_CONTEXT):
        for operation in sorted(
            request.operations,
            key=lambda item: (
                item.occurred_at,
                item.execution_operation_id or item.operation_id,
                item.execution_operation_id is not None,
                item.operation_id,
            ),
        ):
            before_positions = {
                key: (state.quantity, state.cost_currency, state.cost_basis)
                for key, state in positions.items()
            }
            before_realised = {
                key: state.realised_pnl for key, state in metrics.items()
            }
            diagnostic_offset = len(diagnostics)
            _apply_operation(operation, positions, cash, metrics, diagnostics)

            basis_changes: list[CalculationBasisEffect] = []
            for position_key in sorted(
                set(before_positions) | set(positions),
                key=_position_key,
            ):
                before_position = before_positions.get(
                    position_key,
                    (_ZERO, None, _ZERO),
                )
                after_state = positions.get(position_key)
                after_position = (
                    (after_state.quantity, after_state.cost_currency, after_state.cost_basis)
                    if after_state is not None
                    else (_ZERO, None, _ZERO)
                )
                if before_position == after_position:
                    continue
                basis_changes.append(
                    CalculationBasisEffect(
                        account_id=position_key[0],
                        instrument_id=position_key[1],
                        quantity_before=before_position[0],
                        quantity_after=after_position[0],
                        cost_currency_before=before_position[1],
                        cost_currency_after=after_position[1],
                        cost_basis_before=before_position[2],
                        cost_basis_after=after_position[2],
                    )
                )

            realised_pnl: list[CalculationRealisedPnlEffect] = []
            realised_key = _realised_pnl_key(operation)
            for metric_key in sorted(
                set(before_realised) | set(metrics),
                key=_metric_key,
            ):
                before_realised_value = before_realised.get(metric_key, _ZERO)
                after_realised_value = metrics[metric_key].realised_pnl
                if (
                    before_realised_value == after_realised_value
                    and metric_key != realised_key
                ):
                    continue
                realised_pnl.append(
                    CalculationRealisedPnlEffect(
                        account_id=metric_key[0],
                        currency=metric_key[1],
                        amount=_money(after_realised_value - before_realised_value),
                    )
                )
            effects.append(
                CalculationOperationEffect(
                    operation_id=operation.operation_id,
                    account_id=operation.account_id,
                    occurred_at=operation.occurred_at,
                    realised_pnl_status=_realised_pnl_status(operation, realised_pnl),
                    realised_pnl=tuple(realised_pnl),
                    basis_changes=tuple(basis_changes),
                    diagnostics=tuple(diagnostics[diagnostic_offset:]),
                )
            )

    return CalculationOperationEffectsOutput(
        effects=tuple(effects),
        diagnostics=tuple(diagnostics),
    )


def _position_key(value: tuple[UUID, UUID]) -> tuple[str, str]:
    return str(value[0]), str(value[1])


def _metric_key(value: tuple[UUID, str]) -> tuple[str, str]:
    return str(value[0]), value[1]


def _realised_pnl_applies(operation: CalculationOperation) -> bool:
    return _realised_pnl_key(operation) is not None


def _realised_pnl_key(operation: CalculationOperation) -> tuple[UUID, str] | None:
    event = operation.event
    if isinstance(event, TradeEvent) and event.side is CalculationTradeSide.SELL:
        return operation.account_id, event.price_currency
    if isinstance(event, BondRedemptionEvent):
        return operation.account_id, event.currency
    return None


def _realised_pnl_status(
    operation: CalculationOperation,
    effects: list[CalculationRealisedPnlEffect],
) -> RealisedPnlEffectStatus:
    if not _realised_pnl_applies(operation):
        return RealisedPnlEffectStatus.NOT_APPLICABLE
    if effects:
        return RealisedPnlEffectStatus.AVAILABLE
    return RealisedPnlEffectStatus.UNAVAILABLE


def _apply_operation(
    operation: CalculationOperation,
    positions: dict[tuple[UUID, UUID], _PositionState],
    cash: defaultdict[tuple[UUID, str], Decimal],
    metrics: defaultdict[tuple[UUID, str], _MetricsState],
    diagnostics: list[CalculationDiagnostic],
) -> None:
    event = operation.event
    if isinstance(event, TradeEvent):
        state = _position(positions, operation.account_id, event.instrument_id)
        notional = _money(event.quantity * event.price)
        if event.side is CalculationTradeSide.BUY:
            cash[(operation.account_id, event.price_currency)] -= notional
            _acquire(
                state,
                event.quantity,
                notional,
                event.price_currency,
                operation,
                diagnostics,
            )
        else:
            cash[(operation.account_id, event.price_currency)] += notional
            realised = _dispose(
                state,
                event.quantity,
                notional,
                event.price_currency,
                operation,
                diagnostics,
                realise=True,
            )
            if realised is not None:
                metrics[(operation.account_id, event.price_currency)].realised_pnl += realised
        return
    if isinstance(event, CryptoTradeEvent):
        sold_state = _position(positions, operation.account_id, event.sold_instrument_id)
        bought_state = _position(
            positions, operation.account_id, event.bought_instrument_id
        )
        cost_currency = sold_state.cost_currency
        transferred_basis = _dispose(
            sold_state,
            event.sold_quantity,
            _ZERO,
            cost_currency,
            operation,
            diagnostics,
            realise=False,
        )
        if transferred_basis is not None and cost_currency is not None:
            _acquire(
                bought_state,
                event.bought_quantity,
                transferred_basis,
                cost_currency,
                operation,
                diagnostics,
            )
        else:
            bought_state.quantity = _quantity(
                bought_state.quantity + event.bought_quantity
            )
            _invalidate_basis(
                bought_state,
                operation,
                diagnostics,
                "cost_basis_unknown",
                "Crypto trade source asset does not have a known acquisition cost",
            )
        return
    if isinstance(event, IncomeEvent):
        cash[(operation.account_id, event.currency)] += event.amount
        metrics[(operation.account_id, event.currency)].income += event.amount
        return
    if isinstance(event, FeeEvent):
        cash[(operation.account_id, event.currency)] -= event.amount
        metrics[(operation.account_id, event.currency)].fees += event.amount
        return
    if isinstance(event, AssetFeeEvent):
        state = _position(positions, operation.account_id, event.instrument_id)
        _dispose(
            state,
            event.quantity,
            _ZERO,
            state.cost_currency,
            operation,
            diagnostics,
            realise=False,
        )
        return
    if isinstance(event, TaxEvent):
        cash[(operation.account_id, event.currency)] -= event.amount
        metrics[(operation.account_id, event.currency)].taxes += event.amount
        return
    if isinstance(event, CashMovementEvent):
        direction = (
            Decimal(1)
            if event.direction is CalculationCashDirection.DEPOSIT
            else Decimal(-1)
        )
        cash[(operation.account_id, event.currency)] += direction * event.amount
        return
    if isinstance(event, CurrencyExchangeEvent):
        cash[(operation.account_id, event.sold_currency)] -= event.sold_amount
        cash[(operation.account_id, event.bought_currency)] += event.bought_amount
        return
    if isinstance(event, AssetTransferEvent):
        state = _position(positions, operation.account_id, event.instrument_id)
        if event.direction is CalculationTransferDirection.INBOUND:
            state.quantity = _quantity(state.quantity + event.quantity)
            _invalidate_basis(
                state,
                operation,
                diagnostics,
                "cost_basis_unknown",
                "Inbound asset transfer does not provide acquisition cost",
            )
        else:
            _dispose(
                state,
                event.quantity,
                _ZERO,
                state.cost_currency,
                operation,
                diagnostics,
                realise=False,
            )
        return
    if isinstance(event, CorporateActionEvent):
        state = _position(positions, operation.account_id, event.instrument_id)
        previous_quantity = state.quantity
        state.quantity = _quantity(state.quantity + event.quantity_change)
        if previous_quantity == 0 and event.quantity_change > 0:
            _invalidate_basis(
                state,
                operation,
                diagnostics,
                "cost_basis_unknown",
                "Corporate action created a position without acquisition cost",
            )
        if event.action_type is not CalculationCorporateActionType.SPLIT:
            _add_diagnostic(
                state,
                operation,
                diagnostics,
                "warning",
                "corporate_action_basis_assumption",
                "Corporate action preserved existing total cost basis",
            )
        _diagnose_negative(state, operation, diagnostics)
        return
    if isinstance(event, BondRedemptionEvent):
        state = _position(positions, operation.account_id, event.instrument_id)
        cash[(operation.account_id, event.currency)] += event.amount
        realised = _dispose(
            state,
            event.quantity,
            event.amount,
            event.currency,
            operation,
            diagnostics,
            realise=True,
        )
        if realised is not None:
            metrics[(operation.account_id, event.currency)].realised_pnl += realised
        return
    if isinstance(event, AssetAdjustmentEvent):
        state = _position(positions, operation.account_id, event.instrument_id)
        if event.quantity_change > 0:
            state.quantity = _quantity(state.quantity + event.quantity_change)
            _invalidate_basis(
                state,
                operation,
                diagnostics,
                "cost_basis_unknown",
                "Positive asset adjustment does not provide acquisition cost",
            )
        else:
            _dispose(
                state,
                -event.quantity_change,
                _ZERO,
                state.cost_currency,
                operation,
                diagnostics,
                realise=False,
            )
        return
    if isinstance(event, CashAdjustmentEvent):
        cash[(operation.account_id, event.currency)] += event.amount_change


def _position(
    positions: dict[tuple[UUID, UUID], _PositionState],
    account_id: UUID,
    instrument_id: UUID,
) -> _PositionState:
    key = (account_id, instrument_id)
    if key not in positions:
        positions[key] = _PositionState(account_id=account_id, instrument_id=instrument_id)
    return positions[key]


def _acquire(
    state: _PositionState,
    quantity: Decimal,
    cost: Decimal,
    currency: str,
    operation: CalculationOperation,
    diagnostics: list[CalculationDiagnostic],
) -> None:
    if state.quantity < 0:
        state.quantity = _quantity(state.quantity + quantity)
        _invalidate_basis(
            state,
            operation,
            diagnostics,
            "cost_basis_unknown",
            "Acquisition applied to a negative position",
        )
        _diagnose_negative(state, operation, diagnostics)
        return
    if state.quantity != 0 and state.cost_currency not in {None, currency}:
        state.quantity = _quantity(state.quantity + quantity)
        _invalidate_basis(
            state,
            operation,
            diagnostics,
            "cost_currency_mismatch",
            "Trades for one position use different cost currencies",
        )
        return
    state.quantity = _quantity(state.quantity + quantity)
    state.cost_currency = currency
    if state.cost_basis is not None:
        state.cost_basis = _money(state.cost_basis + cost)


def _dispose(
    state: _PositionState,
    quantity: Decimal,
    proceeds: Decimal,
    currency: str | None,
    operation: CalculationOperation,
    diagnostics: list[CalculationDiagnostic],
    *,
    realise: bool,
) -> Decimal | None:
    previous_quantity = state.quantity
    state.quantity = _quantity(state.quantity - quantity)
    if previous_quantity < quantity:
        _invalidate_basis(
            state,
            operation,
            diagnostics,
            "negative_position",
            "Operation disposes more units than the calculated position contains",
            severity="error",
        )
        return None
    if state.cost_basis is None or previous_quantity <= 0:
        _diagnose_negative(state, operation, diagnostics)
        return None
    if realise and state.cost_currency != currency:
        _invalidate_basis(
            state,
            operation,
            diagnostics,
            "cost_currency_mismatch",
            "Disposal proceeds use a different currency from position cost basis",
        )
        return None
    allocated_basis = (
        state.cost_basis
        if quantity == previous_quantity
        else _money(state.cost_basis * quantity / previous_quantity)
    )
    state.cost_basis = _money(state.cost_basis - allocated_basis)
    if state.quantity == 0:
        state.cost_basis = _ZERO
    if not realise:
        return allocated_basis
    realised = _money(proceeds - allocated_basis)
    if state.realised_pnl is not None:
        state.realised_pnl = _money(state.realised_pnl + realised)
    return realised


def _invalidate_basis(
    state: _PositionState,
    operation: CalculationOperation,
    diagnostics: list[CalculationDiagnostic],
    code: str,
    message: str,
    *,
    severity: str = "warning",
) -> None:
    state.cost_basis = None
    state.realised_pnl = None
    _add_diagnostic(state, operation, diagnostics, severity, code, message)


def _diagnose_negative(
    state: _PositionState,
    operation: CalculationOperation,
    diagnostics: list[CalculationDiagnostic],
) -> None:
    if state.quantity < 0:
        _add_diagnostic(
            state,
            operation,
            diagnostics,
            "error",
            "negative_position",
            "Calculated position quantity is negative",
        )


def _add_diagnostic(
    state: _PositionState,
    operation: CalculationOperation,
    diagnostics: list[CalculationDiagnostic],
    severity: str,
    code: str,
    message: str,
) -> None:
    diagnostic = CalculationDiagnostic(
        severity=severity,
        code=code,
        message=message,
        account_id=state.account_id,
        instrument_id=state.instrument_id,
        operation_id=operation.operation_id,
    )
    if diagnostic not in state.diagnostics:
        state.diagnostics.append(diagnostic)
        diagnostics.append(diagnostic)


def _latest_prices(
    prices: tuple[CalculationPrice, ...],
) -> dict[tuple[UUID, str], CalculationPrice]:
    result: dict[tuple[UUID, str], CalculationPrice] = {}
    for price in sorted(prices, key=lambda item: item.observed_at):
        result[(price.instrument_id, price.currency)] = price
    return result


def _position_result(
    state: _PositionState,
    prices: dict[tuple[UUID, str], CalculationPrice],
    diagnostics: list[CalculationDiagnostic],
) -> CalculatedPosition:
    average_cost = None
    if state.cost_basis is not None and state.quantity > 0:
        average_cost = _price(state.cost_basis / state.quantity)

    price = _select_price(state, prices)
    market_value = None
    unrealised_pnl = None
    if state.quantity != 0 and price is None:
        diagnostic = CalculationDiagnostic(
            severity="warning",
            code="market_price_missing",
            message="No market price is available for the calculated position",
            account_id=state.account_id,
            instrument_id=state.instrument_id,
        )
        state.diagnostics.append(diagnostic)
        diagnostics.append(diagnostic)
    elif price is not None:
        market_value = _money(state.quantity * price.price)
        if state.cost_basis is not None and state.cost_currency == price.currency:
            unrealised_pnl = _money(market_value - state.cost_basis)
        elif state.cost_basis is not None and state.quantity != 0:
            diagnostic = CalculationDiagnostic(
                severity="warning",
                code="valuation_currency_mismatch",
                message="Market price currency differs from position cost currency",
                account_id=state.account_id,
                instrument_id=state.instrument_id,
            )
            state.diagnostics.append(diagnostic)
            diagnostics.append(diagnostic)

    return CalculatedPosition(
        account_id=state.account_id,
        instrument_id=state.instrument_id,
        quantity=state.quantity,
        cost_currency=state.cost_currency,
        average_cost=average_cost,
        cost_basis=state.cost_basis,
        valuation_currency=price.currency if price is not None else None,
        market_price=price.price if price is not None else None,
        market_value=market_value,
        realised_pnl=state.realised_pnl,
        unrealised_pnl=unrealised_pnl,
        diagnostics=tuple(state.diagnostics),
    )


def _select_price(
    state: _PositionState,
    prices: dict[tuple[UUID, str], CalculationPrice],
) -> CalculationPrice | None:
    if state.cost_currency is not None:
        matching = prices.get((state.instrument_id, state.cost_currency))
        if matching is not None:
            return matching
    candidates = [
        value
        for (instrument_id, _), value in prices.items()
        if instrument_id == state.instrument_id
    ]
    return candidates[0] if len(candidates) == 1 else None


def _money(value: Decimal) -> Decimal:
    rounded = value.quantize(_MONEY_QUANTUM, rounding=ROUND_HALF_EVEN)
    return validate_decimal(rounded, MONEY_SPEC)


def _price(value: Decimal) -> Decimal:
    rounded = value.quantize(_PRICE_QUANTUM, rounding=ROUND_HALF_EVEN)
    return validate_decimal(rounded, PRICE_SPEC)


def _quantity(value: Decimal) -> Decimal:
    return validate_decimal(value, QUANTITY_SPEC)
