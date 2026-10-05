from types import SimpleNamespace

import pytest

from imports.models import ImportReconciliationStatus, ImportRowStatus
from imports.reconciliation import ImportReconciliationError, reconcile_import_rows


def _row(
    *,
    candidate: dict[str, object] | None = None,
    data: dict[str, object] | None = None,
) -> SimpleNamespace:
    return SimpleNamespace(
        status=ImportRowStatus.READY,
        normalized_candidate=candidate,
        reconciliation_data=data,
    )


def test_reconciliation_matches_exact_cash_and_position_period_deltas() -> None:
    result = reconcile_import_rows(
        [
            _row(
                candidate={
                    "operation_type": "trade",
                    "payload": {
                        "side": "buy",
                        "quantity": "10",
                        "price": "1000.00",
                        "price_currency": "RUB",
                    },
                },
                data={
                    "position_effects": [
                        {"key": "provider_code:SYN", "change": "10"}
                    ]
                },
            ),
            _row(
                candidate={
                    "operation_type": "cash_movement",
                    "payload": {
                        "direction": "deposit",
                        "amount": "10000.00",
                        "currency": "RUB",
                    },
                }
            ),
            _row(
                data={
                    "controls": [
                        {
                            "kind": "cash",
                            "key": "RUB",
                            "opening": "50000.00",
                            "closing": "50000.00",
                        },
                        {
                            "kind": "position",
                            "key": "provider_code:SYN",
                            "opening": "0",
                            "closing": "10",
                        },
                    ]
                }
            ),
        ]
    )

    assert result.status is ImportReconciliationStatus.MATCHED
    assert result.summary == {
        "status": "matched",
        "check_count": 2,
        "mismatch_count": 0,
        "diagnostics": [],
    }


def test_reconciliation_mismatch_diagnostic_does_not_expose_values_or_keys() -> None:
    result = reconcile_import_rows(
        [
            _row(
                data={
                    "controls": [
                        {
                            "kind": "position",
                            "key": "provider_code:PRIVATE",
                            "opening": "3",
                            "closing": "4",
                        }
                    ]
                }
            )
        ]
    )

    assert result.status is ImportReconciliationStatus.MISMATCH
    assert result.summary is not None
    assert result.summary["mismatch_count"] == 1
    assert "PRIVATE" not in str(result.summary)
    assert "import_initial_state_not_imported" in str(result.summary)


def test_reconciliation_rejects_invalid_control_data() -> None:
    with pytest.raises(ImportReconciliationError) as captured:
        reconcile_import_rows(
            [
                _row(
                    data={
                        "controls": [
                            {
                                "kind": "cash",
                                "key": "RUB",
                                "opening": "NaN",
                                "closing": "1",
                            }
                        ]
                    }
                )
            ]
        )

    assert captured.value.code == "import_reconciliation_invalid"
    assert captured.value.message == "Import reconciliation data is invalid"


def test_reconciliation_accepts_crypto_trade_and_asset_fee_without_cash_effect() -> None:
    result = reconcile_import_rows(
        [
            _row(
                candidate={
                    "operation_type": "crypto_trade",
                    "payload": {
                        "sold_instrument_id": "11111111-2222-4333-8444-555555555555",
                        "sold_quantity": "100",
                        "bought_instrument_id": "22222222-3333-4444-8555-666666666666",
                        "bought_quantity": "5",
                    },
                }
            ),
            _row(
                candidate={
                    "operation_type": "fee",
                    "payload": {
                        "instrument_id": "22222222-3333-4444-8555-666666666666",
                        "quantity": "0.005",
                    },
                }
            ),
        ]
    )

    assert result.status is ImportReconciliationStatus.NOT_AVAILABLE
    assert result.summary is None
