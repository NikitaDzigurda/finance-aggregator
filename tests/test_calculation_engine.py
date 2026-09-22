from dataclasses import replace
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from uuid import UUID

from calculation.contracts import (
    AssetFeeEvent,
    AssetTransferEvent,
    CalculationCashDirection,
    CalculationEvent,
    CalculationInput,
    CalculationOperation,
    CalculationPrice,
    CalculationTradeSide,
    CalculationTransferDirection,
    CashMovementEvent,
    CostBasisMethod,
    CryptoTradeEvent,
    FeeEvent,
    IncomeEvent,
    RealisedPnlEffectStatus,
    TaxEvent,
    TradeEvent,
)
from calculation.engine import calculate_operation_effects, calculate_positions

_ACCOUNT_ID = UUID("11111111-2222-4333-8444-555555555555")
_INSTRUMENT_ID = UUID("22222222-3333-4444-8555-666666666666")
_SECOND_INSTRUMENT_ID = UUID("33333333-4444-4555-8666-777777777777")
_FEE_INSTRUMENT_ID = UUID("44444444-5555-4666-8777-888888888888")
_START = datetime(2026, 8, 1, tzinfo=UTC)


def test_same_timestamp_linked_asset_fee_follows_execution_not_random_uuid() -> None:
    trade = CalculationOperation(
        operation_id=UUID(int=99),
        account_id=_ACCOUNT_ID,
        occurred_at=_START,
        event=TradeEvent(
            CalculationTradeSide.BUY, _INSTRUMENT_ID, Decimal("10"), Decimal("100"), "USD"
        ),
    )
    fee = CalculationOperation(
        operation_id=UUID(int=1),
        account_id=_ACCOUNT_ID,
        occurred_at=_START,
        event=AssetFeeEvent(_INSTRUMENT_ID, Decimal("0.1")),
    )
    request = CalculationInput(
        cost_basis_method=CostBasisMethod.WEIGHTED_AVERAGE, operations=(fee, trade), prices=()
    )
    # The old UUID-only ordering reproducibly produced a spurious negative position.
    assert "negative_position" in {d.code for d in calculate_positions(request).diagnostics}
    linked = replace(fee, execution_operation_id=trade.operation_id)
    for operations in ((linked, trade), (trade, linked)):
        ordered = replace(request, operations=operations)
        result = calculate_positions(ordered)
        assert "negative_position" not in {d.code for d in result.diagnostics}
        assert result.positions[0].quantity == Decimal("9.9")
        assert result.positions[0].cost_basis == Decimal("990")
        effects = calculate_operation_effects(ordered)
        assert [item.operation_id for item in effects.effects] == [
            trade.operation_id,
            fee.operation_id,
        ]


def _operation(
    sequence: int,
    event: CalculationEvent,
) -> CalculationOperation:
    return CalculationOperation(
        operation_id=UUID(f"00000000-0000-4000-8000-{sequence:012d}"),
        account_id=_ACCOUNT_ID,
        occurred_at=_START + timedelta(days=sequence),
        event=event,
    )


def test_weighted_average_buy_buy_partial_sell_is_exact() -> None:
    result = calculate_positions(
        CalculationInput(
            cost_basis_method=CostBasisMethod.WEIGHTED_AVERAGE,
            operations=(
                _operation(
                    1,
                    TradeEvent(
                        side=CalculationTradeSide.BUY,
                        instrument_id=_INSTRUMENT_ID,
                        quantity=Decimal("10"),
                        price=Decimal("100"),
                        price_currency="USD",
                    ),
                ),
                _operation(
                    2,
                    TradeEvent(
                        side=CalculationTradeSide.BUY,
                        instrument_id=_INSTRUMENT_ID,
                        quantity=Decimal("10"),
                        price=Decimal("200"),
                        price_currency="USD",
                    ),
                ),
                _operation(
                    3,
                    TradeEvent(
                        side=CalculationTradeSide.SELL,
                        instrument_id=_INSTRUMENT_ID,
                        quantity=Decimal("5"),
                        price=Decimal("180"),
                        price_currency="USD",
                    ),
                ),
            ),
            prices=(
                CalculationPrice(
                    instrument_id=_INSTRUMENT_ID,
                    price=Decimal("210"),
                    currency="USD",
                    observed_at=_START + timedelta(days=4),
                ),
            ),
        )
    )

    position = result.positions[0]
    assert position.quantity == Decimal("15")
    assert position.average_cost == Decimal("150.000000000000000000")
    assert position.cost_basis == Decimal("2250.000000000000000000")
    assert position.realised_pnl == Decimal("150.000000000000000000")
    assert position.market_value == Decimal("3150.000000000000000000")
    assert position.unrealised_pnl == Decimal("900.000000000000000000")
    assert result.cash_balances[0].amount == Decimal("-2100.000000000000000000")
    assert result.currency_metrics[0].realised_pnl == Decimal("150.000000000000000000")
    assert result.diagnostics == ()


def test_fees_taxes_income_and_cex_quote_cash_stay_separate() -> None:
    result = calculate_positions(
        CalculationInput(
            cost_basis_method=CostBasisMethod.WEIGHTED_AVERAGE,
            operations=(
                _operation(
                    1,
                    CashMovementEvent(
                        direction=CalculationCashDirection.DEPOSIT,
                        amount=Decimal("100000"),
                        currency="USD",
                    ),
                ),
                _operation(
                    2,
                    TradeEvent(
                        side=CalculationTradeSide.BUY,
                        instrument_id=_INSTRUMENT_ID,
                        quantity=Decimal("2"),
                        price=Decimal("30000"),
                        price_currency="USD",
                    ),
                ),
                _operation(
                    3,
                    FeeEvent(amount=Decimal("7.5"), currency="EUR"),
                ),
                _operation(
                    4,
                    TaxEvent(amount=Decimal("12"), currency="USD"),
                ),
                _operation(
                    5,
                    IncomeEvent(amount=Decimal("25"), currency="USD"),
                ),
            ),
            prices=(),
        )
    )

    balances = {item.currency: item.amount for item in result.cash_balances}
    metrics = {item.currency: item for item in result.currency_metrics}
    assert result.positions[0].quantity == Decimal("2")
    assert balances == {
        "EUR": Decimal("-7.500000000000000000"),
        "USD": Decimal("40013.000000000000000000"),
    }
    assert metrics["EUR"].fees == Decimal("7.500000000000000000")
    assert metrics["USD"].taxes == Decimal("12.000000000000000000")
    assert metrics["USD"].income == Decimal("25.000000000000000000")
    assert {item.code for item in result.diagnostics} == {"market_price_missing"}


def test_negative_position_and_missing_price_are_persistable_diagnostics() -> None:
    result = calculate_positions(
        CalculationInput(
            cost_basis_method=CostBasisMethod.WEIGHTED_AVERAGE,
            operations=(
                _operation(
                    1,
                    TradeEvent(
                        side=CalculationTradeSide.SELL,
                        instrument_id=_INSTRUMENT_ID,
                        quantity=Decimal("1"),
                        price=Decimal("100"),
                        price_currency="USD",
                    ),
                ),
            ),
            prices=(),
        )
    )

    assert result.positions[0].quantity == Decimal("-1")
    assert result.positions[0].cost_basis is None
    assert result.positions[0].realised_pnl is None
    assert {(item.severity, item.code) for item in result.diagnostics} == {
        ("error", "negative_position"),
        ("warning", "market_price_missing"),
    }


def test_derived_money_uses_explicit_half_even_rounding() -> None:
    result = calculate_positions(
        CalculationInput(
            cost_basis_method=CostBasisMethod.WEIGHTED_AVERAGE,
            operations=(
                _operation(
                    1,
                    TradeEvent(
                        side=CalculationTradeSide.BUY,
                        instrument_id=_INSTRUMENT_ID,
                        quantity=Decimal("0.000000000000000003"),
                        price=Decimal("0.5"),
                        price_currency="USD",
                    ),
                ),
            ),
            prices=(),
        )
    )

    assert result.positions[0].cost_basis == Decimal("0.000000000000000002")
    assert result.positions[0].average_cost == Decimal("0.666666666666666667")


def test_crypto_trade_transfers_basis_and_asset_fees_reduce_exact_quantities() -> None:
    result = calculate_positions(
        CalculationInput(
            cost_basis_method=CostBasisMethod.WEIGHTED_AVERAGE,
            operations=(
                _operation(
                    1,
                    TradeEvent(
                        side=CalculationTradeSide.BUY,
                        instrument_id=_INSTRUMENT_ID,
                        quantity=Decimal("10"),
                        price=Decimal("10"),
                        price_currency="USD",
                    ),
                ),
                _operation(
                    2,
                    CryptoTradeEvent(
                        sold_instrument_id=_INSTRUMENT_ID,
                        sold_quantity=Decimal("5"),
                        bought_instrument_id=_SECOND_INSTRUMENT_ID,
                        bought_quantity=Decimal("2"),
                    ),
                ),
                _operation(
                    3,
                    AssetFeeEvent(
                        instrument_id=_SECOND_INSTRUMENT_ID,
                        quantity=Decimal("0.1"),
                    ),
                ),
                _operation(
                    4,
                    AssetFeeEvent(
                        instrument_id=_FEE_INSTRUMENT_ID,
                        quantity=Decimal("0.01"),
                    ),
                ),
            ),
            prices=(),
        )
    )

    positions = {item.instrument_id: item for item in result.positions}
    assert positions[_INSTRUMENT_ID].quantity == Decimal("5")
    assert positions[_INSTRUMENT_ID].cost_basis == Decimal("50.000000000000000000")
    assert positions[_SECOND_INSTRUMENT_ID].quantity == Decimal("1.9")
    assert positions[_SECOND_INSTRUMENT_ID].cost_basis == Decimal("47.500000000000000000")
    assert positions[_SECOND_INSTRUMENT_ID].realised_pnl == Decimal(0)
    assert positions[_FEE_INSTRUMENT_ID].quantity == Decimal("-0.01")
    assert positions[_FEE_INSTRUMENT_ID].cost_basis is None
    assert not result.currency_metrics


def test_asset_deposit_and_withdrawal_do_not_create_realised_pnl() -> None:
    result = calculate_positions(
        CalculationInput(
            cost_basis_method=CostBasisMethod.WEIGHTED_AVERAGE,
            operations=(
                _operation(
                    1,
                    AssetTransferEvent(
                        direction=CalculationTransferDirection.INBOUND,
                        instrument_id=_INSTRUMENT_ID,
                        quantity=Decimal("5"),
                    ),
                ),
                _operation(
                    2,
                    AssetTransferEvent(
                        direction=CalculationTransferDirection.OUTBOUND,
                        instrument_id=_INSTRUMENT_ID,
                        quantity=Decimal("2"),
                    ),
                ),
            ),
            prices=(),
        )
    )

    position = result.positions[0]
    assert position.quantity == Decimal("3")
    assert position.cost_basis is None
    assert position.realised_pnl is None
    assert not result.currency_metrics


def test_operation_effects_trace_exact_basis_and_realised_pnl() -> None:
    request = CalculationInput(
        cost_basis_method=CostBasisMethod.WEIGHTED_AVERAGE,
        operations=(
            _operation(
                1,
                TradeEvent(
                    side=CalculationTradeSide.BUY,
                    instrument_id=_INSTRUMENT_ID,
                    quantity=Decimal("10"),
                    price=Decimal("100"),
                    price_currency="USD",
                ),
            ),
            _operation(
                2,
                TradeEvent(
                    side=CalculationTradeSide.SELL,
                    instrument_id=_INSTRUMENT_ID,
                    quantity=Decimal("4"),
                    price=Decimal("150"),
                    price_currency="USD",
                ),
            ),
        ),
        prices=(),
    )

    result = calculate_operation_effects(request)

    buy_basis = result.effects[0].basis_changes[0]
    sell = result.effects[1]
    sell_basis = sell.basis_changes[0]
    assert buy_basis.quantity_before == Decimal(0)
    assert buy_basis.quantity_after == Decimal("10")
    assert buy_basis.cost_basis_after == Decimal("1000.000000000000000000")
    assert sell.realised_pnl_status is RealisedPnlEffectStatus.AVAILABLE
    assert sell.realised_pnl[0].currency == "USD"
    assert sell.realised_pnl[0].amount == Decimal("200.000000000000000000")
    assert sell_basis.quantity_before == Decimal("10")
    assert sell_basis.quantity_after == Decimal("6")
    assert sell_basis.cost_basis_before == Decimal("1000.000000000000000000")
    assert sell_basis.cost_basis_after == Decimal("600.000000000000000000")
    assert result.diagnostics == ()

    unavailable = calculate_operation_effects(
        CalculationInput(
            cost_basis_method=CostBasisMethod.WEIGHTED_AVERAGE,
            operations=(
                _operation(
                    3,
                    TradeEvent(
                        side=CalculationTradeSide.SELL,
                        instrument_id=_SECOND_INSTRUMENT_ID,
                        quantity=Decimal("1"),
                        price=Decimal("10"),
                        price_currency="USD",
                    ),
                ),
            ),
            prices=(),
        )
    )
    assert unavailable.effects[0].realised_pnl_status is RealisedPnlEffectStatus.UNAVAILABLE
    assert unavailable.effects[0].realised_pnl == ()
