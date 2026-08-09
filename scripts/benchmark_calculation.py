from __future__ import annotations

import argparse
import platform
import sys
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from statistics import median
from time import perf_counter
from uuid import UUID

from calculation.contracts import (
    CALCULATION_CONTRACT_VERSION,
    CalculationCashDirection,
    CalculationEvent,
    CalculationInput,
    CalculationOperation,
    CalculationPrice,
    CalculationTradeSide,
    CashMovementEvent,
    CostBasisMethod,
    CurrencyExchangeEvent,
    FeeEvent,
    IncomeEvent,
    TaxEvent,
    TradeEvent,
)
from calculation.engine import calculate_positions

_START = datetime(2020, 1, 1, tzinfo=UTC)


def _uuid(namespace: int, sequence: int) -> UUID:
    return UUID(int=(namespace << 96) + sequence + 1)


def _event(phase: int, instrument_id: UUID, price: Decimal) -> CalculationEvent:
    if phase == 0:
        return TradeEvent(
            side=CalculationTradeSide.BUY,
            instrument_id=instrument_id,
            quantity=Decimal("4"),
            price=price,
            price_currency="USD",
        )
    if phase == 1:
        return FeeEvent(amount=Decimal("0.15"), currency="EUR")
    if phase == 2:
        return IncomeEvent(amount=Decimal("0.50"), currency="USD")
    if phase == 3:
        return TradeEvent(
            side=CalculationTradeSide.BUY,
            instrument_id=instrument_id,
            quantity=Decimal("2"),
            price=price + Decimal("1"),
            price_currency="USD",
        )
    if phase == 4:
        return TradeEvent(
            side=CalculationTradeSide.SELL,
            instrument_id=instrument_id,
            quantity=Decimal("1"),
            price=price + Decimal("2"),
            price_currency="USD",
        )
    if phase == 5:
        return TaxEvent(amount=Decimal("0.10"), currency="USD")
    if phase == 6:
        return CurrencyExchangeEvent(
            sold_amount=Decimal("10"),
            sold_currency="USD",
            bought_amount=Decimal("9"),
            bought_currency="EUR",
        )
    if phase == 7:
        return TradeEvent(
            side=CalculationTradeSide.BUY,
            instrument_id=instrument_id,
            quantity=Decimal("1"),
            price=price + Decimal("1.50"),
            price_currency="USD",
        )
    if phase == 8:
        return TradeEvent(
            side=CalculationTradeSide.SELL,
            instrument_id=instrument_id,
            quantity=Decimal("2"),
            price=price + Decimal("3"),
            price_currency="USD",
        )
    return CashMovementEvent(
        direction=CalculationCashDirection.DEPOSIT,
        amount=Decimal("100"),
        currency="USD",
    )


def build_input(
    operation_count: int,
    account_count: int,
    instrument_count: int,
) -> CalculationInput:
    if operation_count < 1 or account_count < 1 or instrument_count < 1:
        raise ValueError("Benchmark sizes must be positive")

    account_ids = tuple(_uuid(1, index) for index in range(account_count))
    instrument_ids = tuple(_uuid(2, index) for index in range(instrument_count))
    pair_count = account_count * instrument_count
    operations: list[CalculationOperation] = []

    for sequence in range(operation_count):
        pair_index = sequence % pair_count
        account_index = pair_index % account_count
        instrument_index = pair_index // account_count
        phase = (sequence // pair_count) % 10
        price = Decimal(50 + instrument_index % 100) + Decimal("0.25")
        instrument_id = instrument_ids[instrument_index]
        operations.append(
            CalculationOperation(
                operation_id=_uuid(3, sequence),
                account_id=account_ids[account_index],
                occurred_at=_START + timedelta(seconds=sequence),
                event=_event(phase, instrument_id, price),
            )
        )

    observed_at = _START + timedelta(seconds=operation_count + 1)
    prices = tuple(
        CalculationPrice(
            instrument_id=instrument_id,
            price=Decimal(55 + index % 100) + Decimal("0.75"),
            currency="USD",
            observed_at=observed_at,
        )
        for index, instrument_id in enumerate(instrument_ids)
    )
    return CalculationInput(
        cost_basis_method=CostBasisMethod.WEIGHTED_AVERAGE,
        operations=tuple(operations),
        prices=prices,
    )


def run_benchmark(request: CalculationInput, warmups: int, runs: int) -> tuple[float, ...]:
    if warmups < 0 or runs < 1:
        raise ValueError("Warmups must be nonnegative and runs must be positive")

    expected = calculate_positions(request)
    for _ in range(warmups):
        if calculate_positions(request) != expected:
            raise RuntimeError("Calculation output changed between warmup runs")

    durations: list[float] = []
    for _ in range(runs):
        started = perf_counter()
        actual = calculate_positions(request)
        durations.append(perf_counter() - started)
        if actual != expected:
            raise RuntimeError("Calculation output changed between measured runs")
    return tuple(durations)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--operations", type=int, default=100_000)
    parser.add_argument("--accounts", type=int, default=8)
    parser.add_argument("--instruments", type=int, default=250)
    parser.add_argument("--warmups", type=int, default=1)
    parser.add_argument("--runs", type=int, default=5)
    arguments = parser.parse_args()

    request = build_input(arguments.operations, arguments.accounts, arguments.instruments)
    durations = run_benchmark(request, arguments.warmups, arguments.runs)
    median_seconds = median(durations)
    result = calculate_positions(request)

    print(f"contract_version={CALCULATION_CONTRACT_VERSION}")
    print(f"python={platform.python_version()}")
    print(f"platform={platform.platform()}")
    print(f"operations={len(request.operations)}")
    print(f"accounts={arguments.accounts}")
    print(f"instruments={arguments.instruments}")
    print(f"positions={len(result.positions)}")
    print(f"diagnostics={len(result.diagnostics)}")
    print(f"runs={arguments.runs}")
    print(f"minimum_seconds={min(durations):.6f}")
    print(f"median_seconds={median_seconds:.6f}")
    print(f"maximum_seconds={max(durations):.6f}")
    print(f"median_operations_per_second={len(request.operations) / median_seconds:.0f}")


if __name__ == "__main__":
    try:
        main()
    except (RuntimeError, ValueError) as error:
        print(str(error), file=sys.stderr)
        raise SystemExit(2) from error
