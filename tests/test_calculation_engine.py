from datetime import UTC, datetime, timedelta
from decimal import Decimal
from uuid import UUID

from calculation.contracts import (
    CalculationCashDirection,
    CalculationEvent,
    CalculationInput,
    CalculationOperation,
    CalculationPrice,
    CalculationTradeSide,
    CashMovementEvent,
    CostBasisMethod,
    FeeEvent,
    IncomeEvent,
    TaxEvent,
    TradeEvent,
)
from calculation.engine import calculate_positions

_ACCOUNT_ID = UUID("11111111-2222-4333-8444-555555555555")
_INSTRUMENT_ID = UUID("22222222-3333-4444-8555-666666666666")
_START = datetime(2026, 8, 1, tzinfo=UTC)


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
    assert result.currency_metrics[0].realised_pnl == Decimal(
        "150.000000000000000000"
    )
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
