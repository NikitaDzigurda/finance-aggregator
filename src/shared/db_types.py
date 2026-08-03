from __future__ import annotations

from decimal import Decimal

from sqlalchemy.engine import Dialect
from sqlalchemy.types import Numeric, TypeDecorator

from shared.exact import (
    MONEY_SPEC,
    PRICE_SPEC,
    QUANTITY_SPEC,
    RATE_SPEC,
    DecimalSpec,
    validate_decimal,
)


class ExactNumeric(TypeDecorator[Decimal]):
    """NUMERIC type that rejects values before PostgreSQL can round them."""

    impl = Numeric
    cache_ok = True

    def __init__(self, spec: DecimalSpec) -> None:
        self.spec = spec
        super().__init__(precision=spec.precision, scale=spec.scale, asdecimal=True)

    def process_bind_param(self, value: Decimal | None, dialect: Dialect) -> Decimal | None:
        del dialect
        if value is None:
            return None
        if not isinstance(value, Decimal):
            raise TypeError(f"{self.spec.name} database values must use Decimal")
        return validate_decimal(value, self.spec)

    def process_result_value(self, value: Decimal | None, dialect: Dialect) -> Decimal | None:
        del dialect
        if value is None:
            return None
        return validate_decimal(value, self.spec)


class MoneyNumeric(ExactNumeric):
    cache_ok = True

    def __init__(self) -> None:
        super().__init__(MONEY_SPEC)


class QuantityNumeric(ExactNumeric):
    cache_ok = True

    def __init__(self) -> None:
        super().__init__(QUANTITY_SPEC)


class PriceNumeric(ExactNumeric):
    cache_ok = True

    def __init__(self) -> None:
        super().__init__(PRICE_SPEC)


class RateNumeric(ExactNumeric):
    cache_ok = True

    def __init__(self) -> None:
        super().__init__(RATE_SPEC)
