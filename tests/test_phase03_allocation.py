from decimal import Decimal
from uuid import uuid4

from allocation.calculation import (
    AllocationValuationComponent,
    calculate_actual_allocation,
)
from allocation.models import AssetClass
from allocation.service import SYSTEM_CATEGORY_IDS, default_asset_class
from apps.api.main import create_app
from instruments.models import InstrumentType


def test_default_asset_classes_cover_instruments_and_cash() -> None:
    assert default_asset_class(InstrumentType.STOCK) is AssetClass.EQUITY
    assert default_asset_class(InstrumentType.BOND) is AssetClass.FIXED_INCOME
    assert default_asset_class(InstrumentType.ETF) is AssetClass.FUND
    assert default_asset_class(InstrumentType.FUND) is AssetClass.FUND
    assert default_asset_class(InstrumentType.OPTION) is AssetClass.DERIVATIVE
    assert default_asset_class(InstrumentType.CRYPTO_ASSET) is AssetClass.CRYPTO
    assert default_asset_class(InstrumentType.CURRENCY) is AssetClass.CASH
    assert SYSTEM_CATEGORY_IDS[AssetClass.CASH]


def test_openapi_exposes_actual_allocation_without_target_contract() -> None:
    schema = create_app().openapi()
    paths = schema["paths"]
    assert "/api/v1/portfolios/{portfolio_id}/allocation" in paths
    assert "/api/v1/portfolios/{portfolio_id}/allocation/targets" not in paths

    response_schema = schema["components"]["schemas"]["ActualAllocationItemResponse"]
    properties = response_schema["properties"]
    assert "actual_weight" in properties
    assert "target_weight" not in properties
    assert "deviation" not in properties


def test_actual_weights_are_exact_and_missing_component_remains_explicit() -> None:
    first_id = uuid4()
    second_id = uuid4()
    result = calculate_actual_allocation(
        [
            AllocationValuationComponent(first_id, Decimal("1")),
            AllocationValuationComponent(second_id, Decimal("1")),
            AllocationValuationComponent(first_id, Decimal("1")),
            AllocationValuationComponent(second_id, None),
        ]
    )
    assert result.quality == "partial"
    assert result.denominator == Decimal("3")
    assert result.included_components == 3
    assert result.excluded_components == 1
    assert sum((item.weight for item in result.weights), start=Decimal(0)) == 1
    assert result.diagnostic_codes == ("allocation_component_unavailable",)


def test_actual_weights_are_unavailable_for_zero_denominator() -> None:
    category_id = uuid4()
    result = calculate_actual_allocation([AllocationValuationComponent(category_id, Decimal("0"))])
    assert result.quality == "unavailable"
    assert result.denominator == 0
    assert result.weights == ()


def test_negative_component_remains_signed_and_is_explicitly_diagnosed() -> None:
    asset_id = uuid4()
    cash_id = uuid4()
    result = calculate_actual_allocation(
        [
            AllocationValuationComponent(asset_id, Decimal("120250")),
            AllocationValuationComponent(cash_id, Decimal("-114678.125")),
        ]
    )

    assert result.denominator == Decimal("5571.875")
    assert sum((item.weight for item in result.weights), start=Decimal(0)) == 1
    assert any(item.weight < 0 for item in result.weights)
    assert any(item.weight > 1 for item in result.weights)
    assert result.diagnostic_codes == ("allocation_negative_component",)
