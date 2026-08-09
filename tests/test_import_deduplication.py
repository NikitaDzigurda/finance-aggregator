from uuid import UUID

from imports.deduplication import duplicate_override_fingerprint, import_fingerprint


def test_import_fingerprint_prioritizes_external_id_and_separates_components() -> None:
    account_id = UUID("00000000-0000-0000-0000-000000000001")
    trade = {
        "operation_type": "trade",
        "source_operation_id": "SYN-001",
        "occurred_at": "2026-08-03T00:00:00Z",
        "payload": {"quantity": "10"},
    }
    corrected_trade = {
        **trade,
        "occurred_at": "2026-08-04T00:00:00Z",
        "payload": {"quantity": "11"},
    }
    fee = {**trade, "operation_type": "fee", "payload": {"amount": "1.25"}}

    trade_fingerprint = import_fingerprint(
        account_id=account_id,
        source_provider="universal_broker",
        candidate=trade,
    )

    assert trade_fingerprint == import_fingerprint(
        account_id=account_id,
        source_provider="universal_broker",
        candidate=corrected_trade,
    )
    assert trade_fingerprint != import_fingerprint(
        account_id=account_id,
        source_provider="universal_broker",
        candidate=fee,
    )
    assert duplicate_override_fingerprint(
        trade_fingerprint,
        batch_id=UUID("00000000-0000-0000-0000-000000000002"),
        row_id=UUID("00000000-0000-0000-0000-000000000003"),
    ) != trade_fingerprint


def test_economic_fingerprint_changes_with_exact_payload() -> None:
    account_id = UUID("00000000-0000-0000-0000-000000000001")
    base = {
        "operation_type": "cash_movement",
        "occurred_at": "2026-08-03T00:00:00Z",
        "time_precision": "date",
        "payload": {"direction": "deposit", "amount": "100.00", "currency": "USD"},
    }
    changed = {
        **base,
        "payload": {"direction": "deposit", "amount": "100.01", "currency": "USD"},
    }

    assert import_fingerprint(
        account_id=account_id,
        source_provider="universal_broker",
        candidate=base,
    ) != import_fingerprint(
        account_id=account_id,
        source_provider="universal_broker",
        candidate=changed,
    )
