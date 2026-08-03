from typing import Any

import pytest
from pydantic import TypeAdapter, ValidationError

from operations.schemas import OperationCreate

operation_adapter = TypeAdapter(OperationCreate)


def operation_payload(operation_type: str, payload: dict[str, Any]) -> dict[str, Any]:
    return {
        "portfolio_id": "11111111-2222-4333-8444-555555555555",
        "account_id": "22222222-3333-4444-8555-666666666666",
        "operation_type": operation_type,
        "occurred_at": "2026-08-03T10:30:00Z",
        "time_precision": "second",
        "payload": payload,
    }


@pytest.mark.parametrize(
    ("data", "expected_code"),
    [
        (
            operation_payload(
                "trade",
                {
                    "side": "buy",
                    "instrument_id": "33333333-4444-4555-8666-777777777777",
                    "quantity": "0",
                    "price": "10",
                    "price_currency": "USD",
                },
            ),
            "value_not_positive",
        ),
        (
            operation_payload(
                "fee",
                {"amount": 1.25, "currency": "USD"},
            ),
            "decimal_string_required",
        ),
        (
            operation_payload(
                "currency_exchange",
                {
                    "sold_amount": "100",
                    "sold_currency": "USD",
                    "bought_amount": "100",
                    "bought_currency": "USD",
                },
            ),
            "currency_exchange_same_currency",
        ),
        (
            operation_payload(
                "balance_adjustment",
                {
                    "instrument_id": "33333333-4444-4555-8666-777777777777",
                    "quantity_change": "1",
                    "amount_change": "10",
                    "currency": "USD",
                    "reason": "Synthetic reconciliation",
                },
            ),
            "balance_adjustment_shape",
        ),
    ],
)
def test_invalid_operation_combinations_are_rejected(
    data: dict[str, Any],
    expected_code: str,
) -> None:
    with pytest.raises(ValidationError) as error:
        operation_adapter.validate_python(data)

    assert expected_code in {item["type"] for item in error.value.errors()}


def test_operation_time_precision_rejects_false_accuracy() -> None:
    data = operation_payload(
        "cash_movement",
        {"direction": "deposit", "amount": "100", "currency": "USD"},
    )
    data["time_precision"] = "minute"
    data["occurred_at"] = "2026-08-03T10:30:01Z"

    with pytest.raises(ValidationError) as error:
        operation_adapter.validate_python(data)

    assert "datetime_precision" in {item["type"] for item in error.value.errors()}


def test_correction_operation_requires_an_audit_note() -> None:
    data = operation_payload(
        "fee",
        {"amount": "1", "currency": "USD"},
    )
    data["correction_of_operation_id"] = "44444444-5555-4666-8777-888888888888"

    with pytest.raises(ValidationError) as error:
        operation_adapter.validate_python(data)

    assert "correction_note_required" in {item["type"] for item in error.value.errors()}
