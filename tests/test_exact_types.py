from datetime import UTC, datetime
from decimal import Decimal
from uuid import UUID

import pytest
from pydantic import BaseModel, ValidationError

from shared.exact import (
    AssetIdentifier,
    AwareDateTime,
    CurrencyCode,
    Money,
    OperationTimestamp,
    Price,
    Quantity,
    Rate,
    TimePrecision,
)


class ExactValues(BaseModel):
    money: Money
    quantity: Quantity
    price: Price
    rate: Rate


def test_exact_values_remain_decimals_and_serialize_as_strings() -> None:
    payload = {
        "money": "12345678901234567890.123456789012345678",
        "quantity": "0.000000000000000001",
        "price": "99999999999999999999.999999999999999999",
        "rate": "12345678901234.123456789012345678901234",
    }

    values = ExactValues.model_validate(payload)

    assert values.money == Decimal(payload["money"])
    assert values.quantity == Decimal(payload["quantity"])
    assert values.price == Decimal(payload["price"])
    assert values.rate == Decimal(payload["rate"])
    assert values.model_dump(mode="json") == payload
    assert ExactValues.model_json_schema()["$defs"]["Money"]["type"] == "string"


@pytest.mark.parametrize("invalid_value", [0.1, 1, "1e-8", "1,25", "+1.25", "NaN"])
def test_money_rejects_inexact_or_ambiguous_formats(invalid_value: object) -> None:
    with pytest.raises(ValidationError) as error:
        ExactValues.model_validate(
            {
                "money": invalid_value,
                "quantity": "1",
                "price": "1",
                "rate": "1",
            }
        )

    assert error.value.errors()[0]["type"] in {
        "decimal_string_required",
        "decimal_format",
    }


@pytest.mark.parametrize(
    ("field", "value", "expected_code"),
    [
        ("money", "0.0000000000000000001", "decimal_scale"),
        ("quantity", "0.0000000000000000001", "decimal_scale"),
        ("price", "100000000000000000000.000000000000000000", "decimal_precision"),
        ("rate", "0.0000000000000000000000001", "decimal_scale"),
    ],
)
def test_exact_values_reject_unsupported_precision(
    field: str,
    value: str,
    expected_code: str,
) -> None:
    payload = {"money": "1", "quantity": "1", "price": "1", "rate": "1"}
    payload[field] = value

    with pytest.raises(ValidationError) as error:
        ExactValues.model_validate(payload)

    assert error.value.errors()[0]["type"] == expected_code


class ReferencesAndTime(BaseModel):
    currency: CurrencyCode
    asset_id: AssetIdentifier
    occurred_at: AwareDateTime


def test_currency_asset_and_time_have_canonical_json_representations() -> None:
    asset_id = "11111111-2222-4333-8444-555555555555"
    values = ReferencesAndTime.model_validate(
        {
            "currency": "USD",
            "asset_id": asset_id,
            "occurred_at": "2026-08-03T13:30:15.123456+03:00",
        }
    )

    assert values.asset_id == UUID(asset_id)
    assert values.occurred_at == datetime(2026, 8, 3, 10, 30, 15, 123456, tzinfo=UTC)
    assert values.model_dump(mode="json") == {
        "currency": "USD",
        "asset_id": asset_id,
        "occurred_at": "2026-08-03T10:30:15.123456Z",
    }


def test_currency_and_naive_time_are_rejected() -> None:
    with pytest.raises(ValidationError) as currency_error:
        ReferencesAndTime.model_validate(
            {
                "currency": "usd",
                "asset_id": "11111111-2222-4333-8444-555555555555",
                "occurred_at": "2026-08-03T10:30:00Z",
            }
        )
    assert currency_error.value.errors()[0]["type"] == "string_pattern_mismatch"

    with pytest.raises(ValidationError) as time_error:
        ReferencesAndTime.model_validate(
            {
                "currency": "USD",
                "asset_id": "11111111-2222-4333-8444-555555555555",
                "occurred_at": "2026-08-03T10:30:00",
            }
        )
    assert time_error.value.errors()[0]["type"] == "datetime_timezone_required"


def test_source_time_precision_rejects_false_accuracy() -> None:
    timestamp = OperationTimestamp.model_validate(
        {"occurred_at": "2026-08-03T10:30:00Z", "precision": TimePrecision.MINUTE}
    )
    assert timestamp.precision is TimePrecision.MINUTE

    with pytest.raises(ValidationError) as error:
        OperationTimestamp.model_validate(
            {"occurred_at": "2026-08-03T10:30:01Z", "precision": TimePrecision.MINUTE}
        )
    assert error.value.errors()[0]["type"] == "datetime_precision"
