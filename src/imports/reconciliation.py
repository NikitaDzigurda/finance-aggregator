from __future__ import annotations

from collections import Counter, defaultdict
from collections.abc import Sequence
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation, localcontext
from typing import Protocol, TypeGuard

from imports.models import ImportReconciliationStatus, ImportRowStatus

_MAX_RECONCILIATION_ITEMS = 10_000


class ReconciliationRow(Protocol):
    status: ImportRowStatus
    normalized_candidate: dict[str, object] | None
    reconciliation_data: dict[str, object] | None


class ImportReconciliationError(Exception):
    def __init__(self) -> None:
        super().__init__("Import reconciliation data is invalid")
        self.code = "import_reconciliation_invalid"
        self.message = "Import reconciliation data is invalid"


@dataclass(frozen=True, slots=True)
class ReconciliationResult:
    status: ImportReconciliationStatus
    summary: dict[str, object] | None


def reconcile_import_rows(rows: Sequence[ReconciliationRow]) -> ReconciliationResult:
    controls: dict[tuple[str, str], tuple[Decimal, Decimal]] = {}
    effects: defaultdict[tuple[str, str], Decimal] = defaultdict(Decimal)
    item_count = 0

    with localcontext() as context:
        context.prec = 80
        for row in rows:
            data = row.reconciliation_data
            if data is not None:
                item_count = _read_reconciliation_data(
                    data,
                    controls=controls,
                    effects=effects,
                    item_count=item_count,
                )
            if row.status is not ImportRowStatus.ERROR:
                _add_candidate_cash_effect(row.normalized_candidate, effects)

    if not controls:
        return ReconciliationResult(ImportReconciliationStatus.NOT_AVAILABLE, None)

    mismatch_counts: Counter[str] = Counter()
    initial_position_count = 0
    for (kind, key), (opening, closing) in controls.items():
        if effects[(kind, key)] != closing - opening:
            mismatch_counts[kind] += 1
        if kind == "position" and opening != 0:
            initial_position_count += 1

    diagnostics: list[dict[str, object]] = []
    for kind, code, message in (
        (
            "cash",
            "import_reconciliation_cash_mismatch",
            "Calculated cash movement does not match report controls",
        ),
        (
            "position",
            "import_reconciliation_position_mismatch",
            "Calculated position movement does not match report controls",
        ),
    ):
        if mismatch_counts[kind]:
            diagnostics.append(
                {"code": code, "message": message, "count": mismatch_counts[kind]}
            )
    if initial_position_count:
        diagnostics.append(
            {
                "code": "import_initial_state_not_imported",
                "message": (
                    "Report starts with existing positions; their historical cost basis "
                    "is not imported"
                ),
                "count": initial_position_count,
            }
        )

    mismatch_count = sum(mismatch_counts.values())
    reconciliation_status = (
        ImportReconciliationStatus.MISMATCH
        if mismatch_count
        else ImportReconciliationStatus.MATCHED
    )
    return ReconciliationResult(
        reconciliation_status,
        {
            "status": reconciliation_status.value,
            "check_count": len(controls),
            "mismatch_count": mismatch_count,
            "diagnostics": diagnostics,
        },
    )


def _read_reconciliation_data(
    data: dict[str, object],
    *,
    controls: dict[tuple[str, str], tuple[Decimal, Decimal]],
    effects: defaultdict[tuple[str, str], Decimal],
    item_count: int,
) -> int:
    if set(data) - {"controls", "position_effects"}:
        raise ImportReconciliationError
    raw_controls = data.get("controls", [])
    raw_effects = data.get("position_effects", [])
    if not isinstance(raw_controls, list) or not isinstance(raw_effects, list):
        raise ImportReconciliationError
    item_count += len(raw_controls) + len(raw_effects)
    if item_count > _MAX_RECONCILIATION_ITEMS:
        raise ImportReconciliationError

    for item in raw_controls:
        if not isinstance(item, dict) or set(item) != {"kind", "key", "opening", "closing"}:
            raise ImportReconciliationError
        kind = item.get("kind")
        key = item.get("key")
        if not isinstance(kind, str) or kind not in {"cash", "position"}:
            raise ImportReconciliationError
        if not _valid_key(key):
            raise ImportReconciliationError
        control_key = (kind, key)
        opening = _decimal(item.get("opening"))
        closing = _decimal(item.get("closing"))
        previous_opening, previous_closing = controls.get(
            control_key, (Decimal(0), Decimal(0))
        )
        controls[control_key] = (
            previous_opening + opening,
            previous_closing + closing,
        )

    for item in raw_effects:
        if not isinstance(item, dict) or set(item) != {"key", "change"}:
            raise ImportReconciliationError
        key = item.get("key")
        if not _valid_key(key):
            raise ImportReconciliationError
        effects[("position", key)] += _decimal(item.get("change"))
    return item_count


def _add_candidate_cash_effect(
    candidate: dict[str, object] | None,
    effects: defaultdict[tuple[str, str], Decimal],
) -> None:
    if candidate is None:
        return
    operation_type = candidate.get("operation_type")
    payload = candidate.get("payload")
    if not isinstance(operation_type, str) or not isinstance(payload, dict):
        raise ImportReconciliationError

    if operation_type == "trade":
        currency = _currency(payload.get("price_currency"))
        quantity = _decimal(payload.get("quantity"))
        price = _decimal(payload.get("price"))
        side = payload.get("side")
        if side not in {"buy", "sell"}:
            raise ImportReconciliationError
        amount = quantity * price
        effects[("cash", currency)] += -amount if side == "buy" else amount
    elif operation_type in {"income", "tax"}:
        currency = _currency(payload.get("currency"))
        amount = _decimal(payload.get("amount"))
        effects[("cash", currency)] += amount if operation_type == "income" else -amount
    elif operation_type == "fee":
        if "amount" in payload or "currency" in payload:
            currency = _currency(payload.get("currency"))
            effects[("cash", currency)] -= _decimal(payload.get("amount"))
        elif "quantity" not in payload or "instrument_id" not in payload:
            raise ImportReconciliationError
    elif operation_type == "cash_movement":
        currency = _currency(payload.get("currency"))
        amount = _decimal(payload.get("amount"))
        direction = payload.get("direction")
        if direction not in {"deposit", "withdrawal"}:
            raise ImportReconciliationError
        effects[("cash", currency)] += amount if direction == "deposit" else -amount
    elif operation_type == "currency_exchange":
        sold_currency = _currency(payload.get("sold_currency"))
        bought_currency = _currency(payload.get("bought_currency"))
        effects[("cash", sold_currency)] -= _decimal(payload.get("sold_amount"))
        effects[("cash", bought_currency)] += _decimal(payload.get("bought_amount"))
    elif operation_type == "bond_redemption":
        currency = _currency(payload.get("currency"))
        effects[("cash", currency)] += _decimal(payload.get("amount"))
    elif operation_type == "balance_adjustment" and payload.get("currency") is not None:
        currency = _currency(payload.get("currency"))
        effects[("cash", currency)] += _decimal(payload.get("amount_change"))


def _valid_key(value: object) -> TypeGuard[str]:
    return isinstance(value, str) and 0 < len(value) <= 256 and value.strip() == value


def _currency(value: object) -> str:
    if not _valid_key(value):
        raise ImportReconciliationError
    return value


def _decimal(value: object) -> Decimal:
    if not isinstance(value, str) or len(value) > 128:
        raise ImportReconciliationError
    try:
        result = Decimal(value)
    except InvalidOperation as exc:
        raise ImportReconciliationError from exc
    if not result.is_finite():
        raise ImportReconciliationError
    return result
