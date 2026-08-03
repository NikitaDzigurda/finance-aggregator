from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal
from enum import StrEnum
from typing import Annotated, Self
from uuid import UUID

from pydantic import (
    AfterValidator,
    BaseModel,
    BeforeValidator,
    ConfigDict,
    PlainSerializer,
    StringConstraints,
    WithJsonSchema,
    model_validator,
)
from pydantic_core import PydanticCustomError

DECIMAL_PATTERN = r"^-?(?:0|[1-9][0-9]*)(?:\.[0-9]+)?$"
_DECIMAL_RE = re.compile(DECIMAL_PATTERN)
_MAX_DECIMAL_INPUT_LENGTH = 128


@dataclass(frozen=True, slots=True)
class DecimalSpec:
    """Precision contract shared by Pydantic and SQLAlchemy types."""

    name: str
    precision: int
    scale: int
    example: str


MONEY_SPEC = DecimalSpec("Money", precision=38, scale=18, example="1250.75")
QUANTITY_SPEC = DecimalSpec("Quantity", precision=38, scale=18, example="0.00000001")
PRICE_SPEC = DecimalSpec("Price", precision=38, scale=18, example="102.3456")
RATE_SPEC = DecimalSpec("Rate", precision=38, scale=24, example="1.0123456789")


class ExactDecimalError(ValueError):
    """Machine-readable exact-decimal validation failure."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


def parse_decimal_input(value: object) -> Decimal:
    """Accept exact internal Decimals and plain decimal strings from JSON."""
    if isinstance(value, Decimal):
        return value
    if not isinstance(value, str):
        raise PydanticCustomError(
            "decimal_string_required",
            "Exact decimal values must be sent as JSON strings",
        )
    if len(value) > _MAX_DECIMAL_INPUT_LENGTH or _DECIMAL_RE.fullmatch(value) is None:
        raise PydanticCustomError(
            "decimal_format",
            "Value must use plain decimal notation without exponent or separators",
        )
    return Decimal(value)


def validate_decimal(value: Decimal, spec: DecimalSpec) -> Decimal:
    """Reject non-finite values and values that PostgreSQL would round."""
    if not value.is_finite():
        raise ExactDecimalError("decimal_not_finite", "Value must be finite")

    _, digits, exponent = value.as_tuple()
    assert isinstance(exponent, int)
    scale = max(-exponent, 0)
    integer_digits = len(digits) + exponent if exponent >= 0 else max(len(digits) - scale, 0)
    precision = integer_digits + scale

    if scale > spec.scale:
        raise ExactDecimalError(
            "decimal_scale",
            f"{spec.name} supports at most {spec.scale} fractional digits",
        )
    if precision > spec.precision:
        raise ExactDecimalError(
            "decimal_precision",
            f"{spec.name} supports at most {spec.precision} total digits",
        )
    return value


def decimal_to_json(value: Decimal) -> str:
    """Serialize without exponent notation, binary conversion, or rounding."""
    return format(value, "f")


def _validate_for_pydantic(value: Decimal, spec: DecimalSpec) -> Decimal:
    try:
        return validate_decimal(value, spec)
    except ExactDecimalError as exc:
        raise PydanticCustomError(exc.code, exc.message) from exc


def _validate_money(value: Decimal) -> Decimal:
    return _validate_for_pydantic(value, MONEY_SPEC)


def _validate_quantity(value: Decimal) -> Decimal:
    return _validate_for_pydantic(value, QUANTITY_SPEC)


def _validate_price(value: Decimal) -> Decimal:
    return _validate_for_pydantic(value, PRICE_SPEC)


def _validate_rate(value: Decimal) -> Decimal:
    return _validate_for_pydantic(value, RATE_SPEC)


def _decimal_json_schema(spec: DecimalSpec) -> dict[str, object]:
    return {
        "type": "string",
        "pattern": DECIMAL_PATTERN,
        "description": (
            f"Exact {spec.name} decimal; maximum {spec.precision} total and "
            f"{spec.scale} fractional digits. JSON numbers are not accepted."
        ),
        "examples": [spec.example],
    }


type Money = Annotated[
    Decimal,
    BeforeValidator(parse_decimal_input),
    AfterValidator(_validate_money),
    PlainSerializer(decimal_to_json, return_type=str, when_used="json"),
    WithJsonSchema(_decimal_json_schema(MONEY_SPEC)),
]
type Quantity = Annotated[
    Decimal,
    BeforeValidator(parse_decimal_input),
    AfterValidator(_validate_quantity),
    PlainSerializer(decimal_to_json, return_type=str, when_used="json"),
    WithJsonSchema(_decimal_json_schema(QUANTITY_SPEC)),
]
type Price = Annotated[
    Decimal,
    BeforeValidator(parse_decimal_input),
    AfterValidator(_validate_price),
    PlainSerializer(decimal_to_json, return_type=str, when_used="json"),
    WithJsonSchema(_decimal_json_schema(PRICE_SPEC)),
]
type Rate = Annotated[
    Decimal,
    BeforeValidator(parse_decimal_input),
    AfterValidator(_validate_rate),
    PlainSerializer(decimal_to_json, return_type=str, when_used="json"),
    WithJsonSchema(_decimal_json_schema(RATE_SPEC)),
]

type CurrencyCode = Annotated[
    str,
    StringConstraints(strict=True, min_length=3, max_length=3, pattern=r"^[A-Z]{3}$"),
]


def _parse_asset_identifier(value: object) -> UUID:
    if isinstance(value, UUID):
        return value
    if not isinstance(value, str):
        raise PydanticCustomError(
            "asset_identifier_string_required",
            "Asset identifier must be a canonical UUID string",
        )
    try:
        parsed = UUID(value)
    except ValueError as exc:
        raise PydanticCustomError(
            "asset_identifier_format",
            "Asset identifier must be a canonical UUID string",
        ) from exc
    if str(parsed) != value:
        raise PydanticCustomError(
            "asset_identifier_format",
            "Asset identifier must be a canonical UUID string",
        )
    return parsed


type AssetIdentifier = Annotated[
    UUID,
    BeforeValidator(_parse_asset_identifier),
    PlainSerializer(str, return_type=str, when_used="json"),
    WithJsonSchema({"type": "string", "format": "uuid"}),
]


def _require_datetime_input(value: object) -> object:
    if not isinstance(value, (str, datetime)):
        raise PydanticCustomError(
            "datetime_string_required",
            "Date and time must be sent as an ISO 8601 string",
        )
    return value


def _validate_aware_datetime(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise PydanticCustomError(
            "datetime_timezone_required",
            "Date and time must include a UTC offset or Z suffix",
        )
    return value.astimezone(UTC)


def _datetime_to_json(value: datetime) -> str:
    return value.astimezone(UTC).isoformat().replace("+00:00", "Z")


type AwareDateTime = Annotated[
    datetime,
    BeforeValidator(_require_datetime_input),
    AfterValidator(_validate_aware_datetime),
    PlainSerializer(_datetime_to_json, return_type=str, when_used="json"),
    WithJsonSchema({"type": "string", "format": "date-time"}),
]


class TimePrecision(StrEnum):
    """Precision actually present in the source, independent of storage precision."""

    DATE = "date"
    MINUTE = "minute"
    SECOND = "second"
    MILLISECOND = "millisecond"
    MICROSECOND = "microsecond"


class OperationTimestamp(BaseModel):
    """Timezone-aware operation instant paired with declared source precision."""

    model_config = ConfigDict(frozen=True)

    occurred_at: AwareDateTime
    precision: TimePrecision

    @model_validator(mode="after")
    def validate_declared_precision(self) -> Self:
        value = self.occurred_at
        if self.precision is TimePrecision.MINUTE and (value.second or value.microsecond):
            raise PydanticCustomError(
                "datetime_precision",
                "Minute precision cannot include seconds or fractional seconds",
            )
        if self.precision is TimePrecision.SECOND and value.microsecond:
            raise PydanticCustomError(
                "datetime_precision",
                "Second precision cannot include fractional seconds",
            )
        if self.precision is TimePrecision.MILLISECOND and value.microsecond % 1000:
            raise PydanticCustomError(
                "datetime_precision",
                "Millisecond precision cannot include microsecond-only digits",
            )
        return self
