from __future__ import annotations

import csv
import re
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from decimal import Decimal, InvalidOperation
from io import StringIO

from imports.adapters import (
    DetectedDocument,
    DetectionResult,
    ImportDocument,
    ParsedImport,
    ParsedRow,
    ValidationResult,
)
from imports.models import ImportCompleteness, ImportFileFormat, ImportRowStatus

type Diagnostic = dict[str, object]

_MAX_ROWS = 100_000
_MAX_VALUE_LENGTH = 10_000
_DECIMAL = re.compile(r"^[+-]?[0-9]+(?:\.[0-9]+)?$")
_UID_METADATA = re.compile(r"^UID:\s*([^,]+)")
_ROLE_HEADERS = {
    "spot_trade_history": frozenset(
        {
            "Uid",
            "Spot Pairs",
            "Order Type",
            "Direction",
            "Filled Value",
            "Filled Price",
            "Filled Quantity",
            "Fees",
            "Transaction ID",
            "Order No.",
            "Timestamp (UTC+0)",
        }
    ),
    "uta_asset_change_details": frozenset(
        {
            "Uid",
            "Currency",
            "Contract",
            "Type",
            "Direction",
            "Quantity",
            "Position",
            "Filled Price",
            "Funding",
            "Fee Paid",
            "Cash Flow",
            "Change",
            "Wallet Balance",
            "Action",
            "Time(UTC)",
        }
    ),
    "funding_asset_change_details": frozenset(
        {
            "Uid",
            "Date & Time(UTC)",
            "Coin",
            "QTY",
            "Type",
            "Account Balance",
            "Description",
        }
    ),
    "withdraw_deposit_history": frozenset(
        {
            "Uid",
            "Date",
            "Type",
            "Asset",
            "Chain",
            "Amount",
            "Tx ID",
            "Status",
            "Received Address",
        }
    ),
}


@dataclass(frozen=True, slots=True)
class _SourceRow:
    document_index: int
    source_row_number: int
    values: dict[str, str]

    @property
    def raw_data(self) -> dict[str, object]:
        return {key: value for key, value in self.values.items() if key != "Uid"}


@dataclass(frozen=True, slots=True)
class _Table:
    document_index: int
    role: str | None
    uid: str | None
    rows: tuple[_SourceRow, ...]
    diagnostics: tuple[Diagnostic, ...] = ()


class BybitSpotCsvBundleAdapter:
    format_id = "bybit_spot_csv_bundle"
    version = "1.0"
    supported_file_formats = frozenset({ImportFileFormat.CSV})

    def detect(self, document: ImportDocument) -> DetectionResult:
        tables = _read_bundle(document)
        diagnostics = _bundle_diagnostics(tables)
        recognized = any(table.role is not None for table in tables)
        timestamps = _all_timestamps(tables)
        return DetectionResult(
            matched=recognized,
            completeness=ImportCompleteness.PERIOD_LEDGER,
            diagnostics=diagnostics,
            reporting_period_start=min(timestamps).date() if timestamps else None,
            reporting_period_end=max(timestamps).date() if timestamps else None,
            documents=tuple(
                DetectedDocument(table.document_index, table.role)
                for table in tables
                if table.role is not None
            ),
        )

    def parse(self, document: ImportDocument) -> ParsedImport:
        tables = _read_bundle(document)
        diagnostics = _bundle_diagnostics(tables)
        if diagnostics:
            return ParsedImport(
                rows=(
                    ParsedRow(
                        sequence_number=1,
                        source_document_index=0,
                        raw_data={},
                        errors=diagnostics,
                    ),
                ),
                diagnostics=diagnostics,
            )
        return ParsedImport(rows=_parse_tables(tables))

    def validate(self, parsed: ParsedImport) -> ValidationResult:
        return ValidationResult(rows=parsed.rows, diagnostics=parsed.diagnostics)


def _documents(document: ImportDocument) -> tuple[ImportDocument, ...]:
    return document.bundle_documents or (document,)


def _read_bundle(document: ImportDocument) -> tuple[_Table, ...]:
    return tuple(_read_table(item) for item in _documents(document))


def _read_table(document: ImportDocument) -> _Table:
    document.stream.seek(0)
    payload = document.stream.read()
    document.stream.seek(0)
    try:
        text = payload.decode("utf-8-sig")
    except UnicodeDecodeError:
        return _table_error(document, "bybit_csv_encoding_invalid", "CSV must use UTF-8")
    try:
        rows = list(csv.reader(StringIO(text, newline="")))
    except csv.Error:
        return _table_error(document, "bybit_csv_structure_invalid", "CSV structure is invalid")
    if len(rows) < 3:
        return _table_error(document, "bybit_csv_rows_missing", "CSV has no data rows")
    uid_match = _UID_METADATA.match(rows[0][0].strip()) if rows[0] else None
    uid = uid_match.group(1).strip() if uid_match is not None else None
    headers = rows[1]
    if len(headers) != len(set(headers)) or any(not item.strip() for item in headers):
        return _table_error(document, "bybit_csv_header_invalid", "CSV header is invalid")
    header_set = frozenset(headers)
    roles = [role for role, required in _ROLE_HEADERS.items() if required == header_set]
    if len(roles) != 1:
        return _table_error(
            document,
            "bybit_csv_document_type_unknown",
            "CSV does not match a supported Bybit export document",
        )
    source_rows: list[_SourceRow] = []
    for number, values in enumerate(rows[2:], start=3):
        if not any(value.strip() for value in values):
            continue
        if len(source_rows) >= _MAX_ROWS:
            return _table_error(
                document, "bybit_csv_row_limit", "CSV contains too many rows"
            )
        if len(values) != len(headers) or any(len(value) > _MAX_VALUE_LENGTH for value in values):
            return _table_error(
                document,
                "bybit_csv_value_invalid",
                "CSV row shape or value length is invalid",
                source_row_number=number,
            )
        source_rows.append(
            _SourceRow(
                document_index=document.document_index,
                source_row_number=number,
                values=dict(zip(headers, (value.strip() for value in values), strict=True)),
            )
        )
    if not source_rows:
        return _table_error(document, "bybit_csv_rows_missing", "CSV has no data rows")
    return _Table(document.document_index, roles[0], uid, tuple(source_rows))


def _table_error(
    document: ImportDocument, code: str, message: str, **details: object
) -> _Table:
    diagnostic: Diagnostic = {"code": code, "message": message}
    diagnostic.update(details)
    return _Table(document.document_index, None, None, (), (diagnostic,))


def _bundle_diagnostics(tables: tuple[_Table, ...]) -> tuple[Diagnostic, ...]:
    diagnostics = [item for table in tables for item in table.diagnostics]
    roles = [table.role for table in tables if table.role is not None]
    missing = sorted(set(_ROLE_HEADERS) - set(roles))
    duplicates = sorted(role for role in set(roles) if roles.count(role) > 1)
    if len(tables) != 4 or missing or duplicates:
        diagnostics.append(
            {
                "code": "bybit_bundle_documents_invalid",
                "message": "Bybit Spot import requires one document of each supported type",
                "missing_document_types": missing,
                "duplicate_document_types": duplicates,
            }
        )
    uids = {table.uid for table in tables if table.uid}
    if any(table.uid is None for table in tables) or len(uids) != 1:
        diagnostics.append(
            {
                "code": "bybit_bundle_uid_mismatch",
                "message": "Bybit bundle documents do not contain one consistent UID",
            }
        )
    for table in tables:
        if table.uid is not None and any(
            row.values.get("Uid") != table.uid for row in table.rows
        ):
            diagnostics.append(
                {
                    "code": "bybit_document_uid_mismatch",
                    "message": "A Bybit document contains inconsistent UID values",
                    "document_index": table.document_index,
                }
            )
    return tuple(diagnostics)


def _parse_tables(tables: tuple[_Table, ...]) -> tuple[ParsedRow, ...]:
    by_role = {table.role: table for table in tables}
    spot = by_role["spot_trade_history"]
    uta = by_role["uta_asset_change_details"]
    funding = by_role["funding_asset_change_details"]
    transfer = by_role["withdraw_deposit_history"]
    result: list[ParsedRow] = []
    sequence = 1

    uta_trade_rows = [row for row in uta.rows if row.values["Type"] == "TRADE"]
    for spot_row in spot.rows:
        parsed_rows = _parse_spot_execution(spot_row, uta_trade_rows, sequence)
        result.extend(parsed_rows)
        sequence += len(parsed_rows)

    matched_internal_funding: set[int] = set()
    for row in uta.rows:
        if row.values["Type"] == "TRADE":
            continue
        internal_match = _match_internal_transfer(row, funding.rows)
        if internal_match is None:
            result.append(
                ParsedRow(
                    sequence_number=sequence,
                    source_document_index=row.document_index,
                    source_row_number=row.source_row_number,
                    raw_data=row.raw_data,
                    warnings=(
                        {
                            "code": "bybit_internal_transfer_match_invalid",
                            "message": "Internal UTA movement has no exact Funding counterpart",
                        },
                    ),
                )
            )
        else:
            matched_internal_funding.add(internal_match.source_row_number)
            result.append(_excluded(row, sequence, "bybit_internal_transfer_ignored"))
        sequence += 1

    matched_onchain_funding: set[int] = set()
    for row in transfer.rows:
        parsed_rows, funding_row = _parse_onchain_transfer(row, funding.rows, sequence)
        result.extend(parsed_rows)
        sequence += len(parsed_rows)
        if funding_row is not None:
            matched_onchain_funding.add(funding_row.source_row_number)

    for row in funding.rows:
        if row.source_row_number in matched_onchain_funding:
            continue
        if row.source_row_number in matched_internal_funding:
            result.append(_excluded(row, sequence, "bybit_internal_transfer_ignored"))
            sequence += 1
            continue
        normalized_type = row.values["Type"].strip().casefold()
        if normalized_type in {"transfer in", "transfer out"}:
            result.append(
                ParsedRow(
                    sequence_number=sequence,
                    source_document_index=row.document_index,
                    source_row_number=row.source_row_number,
                    raw_data=row.raw_data,
                    warnings=(
                        {
                            "code": "bybit_internal_transfer_match_invalid",
                            "message": "Funding transfer has no exact UTA counterpart",
                        },
                    ),
                )
            )
        else:
            result.append(_parse_funding_adjustment(row, sequence))
        sequence += 1
    return tuple(result)


def _match_internal_transfer(
    uta_row: _SourceRow, funding_rows: tuple[_SourceRow, ...]
) -> _SourceRow | None:
    uta_type = uta_row.values["Type"]
    expected_funding_type = {
        "TRANSFER_OUT": "transfer in",
        "TRANSFER_IN": "transfer out",
    }.get(uta_type)
    if expected_funding_type is None:
        return None
    errors: list[Diagnostic] = []
    uta_amount = _number(uta_row.values["Change"], "Change", errors)
    if errors or uta_amount is None or uta_amount == 0:
        return None
    matches: list[_SourceRow] = []
    for funding_row in funding_rows:
        if (
            funding_row.values["Type"].strip().casefold() != expected_funding_type
            or funding_row.values["Coin"] != uta_row.values["Currency"]
        ):
            continue
        funding_errors: list[Diagnostic] = []
        funding_amount = _number(funding_row.values["QTY"], "QTY", funding_errors)
        if funding_errors or funding_amount is None or abs(funding_amount) != abs(uta_amount):
            continue
        timestamp_errors: list[Diagnostic] = []
        uta_time = _timestamp(uta_row.values["Time(UTC)"], timestamp_errors)
        funding_time = _timestamp(
            funding_row.values["Date & Time(UTC)"], timestamp_errors
        )
        if timestamp_errors or uta_time is None or funding_time is None:
            continue
        uta_datetime = datetime.fromisoformat(uta_time.replace("Z", "+00:00"))
        funding_datetime = datetime.fromisoformat(funding_time.replace("Z", "+00:00"))
        if abs(funding_datetime - uta_datetime) <= timedelta(seconds=1):
            matches.append(funding_row)
    return matches[0] if len(matches) == 1 else None


def _parse_spot_execution(
    spot: _SourceRow, uta_rows: list[_SourceRow], sequence: int
) -> tuple[ParsedRow, ...]:
    errors: list[Diagnostic] = []
    occurred_at = _timestamp(spot.values["Timestamp (UTC+0)"], errors)
    filled_value = _number(spot.values["Filled Value"], "Filled Value", errors)
    filled_quantity = _number(spot.values["Filled Quantity"], "Filled Quantity", errors)
    fees = _number(spot.values["Fees"], "Fees", errors)
    matches = [
        row
        for row in uta_rows
        if row.values["Contract"] == spot.values["Spot Pairs"]
        and row.values["Direction"] == spot.values["Direction"]
        and row.values["Time(UTC)"] == spot.values["Timestamp (UTC+0)"]
    ]
    quantities = [(row, _number(row.values["Quantity"], "Quantity", errors)) for row in matches]
    valid_quantities = [(row, value) for row, value in quantities if value is not None]
    expected_negative: Decimal | None = None
    expected_positive: Decimal | None = None
    if filled_value is not None and filled_quantity is not None:
        if spot.values["Direction"] == "BUY":
            expected_negative = -filled_value
            expected_positive = filled_quantity
        elif spot.values["Direction"] == "SELL":
            expected_negative = -filled_quantity
            expected_positive = filled_value
    negatives = [
        (row, value)
        for row, value in valid_quantities
        if expected_negative is not None and value == expected_negative
    ]
    positives = [
        (row, value)
        for row, value in valid_quantities
        if expected_positive is not None and value == expected_positive
    ]
    if len(negatives) != 1 or len(positives) != 1:
        errors.append(
            {
                "code": "bybit_trade_legs_invalid",
                "message": "Spot execution must match exactly two signed UTA trade legs",
            }
        )
    if errors or occurred_at is None or not negatives or not positives:
        return (
            ParsedRow(
                sequence_number=sequence,
                source_document_index=spot.document_index,
                source_row_number=spot.source_row_number,
                raw_data=spot.raw_data,
                errors=tuple(errors),
            ),
        )

    sold_row, sold_quantity = negatives[0]
    bought_row, bought_quantity = positives[0]
    matched_rows = (sold_row, bought_row)
    transaction_id = spot.values["Transaction ID"]
    common = {"occurred_at": occurred_at, "time_precision": "second"}
    linked_rows = [
        {"document_index": row.document_index, "source_row_number": row.source_row_number}
        for row in matched_rows
    ]
    trade = ParsedRow(
        sequence_number=sequence,
        source_document_index=spot.document_index,
        source_row_number=spot.source_row_number,
        raw_data={**spot.raw_data, "linked_source_rows": linked_rows},
        normalized_candidate={
            **common,
            "source_operation_id": f"{transaction_id}:trade",
            "operation_type": "crypto_trade",
            "payload": {
                "sold_instrument_reference": _asset_reference(sold_row.values["Currency"]),
                "sold_quantity": _decimal_json(abs(sold_quantity)),
                "bought_instrument_reference": _asset_reference(
                    bought_row.values["Currency"]
                ),
                "bought_quantity": _decimal_json(bought_quantity),
            },
        },
    )
    fee_legs = []
    for row in matched_rows:
        fee = _number(row.values["Fee Paid"], "Fee Paid", errors)
        if fee is not None and fee < 0:
            fee_legs.append((row, fee))
    if fees is None or fees == 0:
        return (trade,)
    if len(fee_legs) != 1 or abs(fee_legs[0][1]) != fees:
        return (
            trade,
            ParsedRow(
                sequence_number=sequence + 1,
                source_document_index=spot.document_index,
                source_row_number=spot.source_row_number,
                raw_data=spot.raw_data,
                errors=(
                    {
                        "code": "bybit_trade_fee_mismatch",
                        "message": "Spot fee does not match exactly one UTA fee leg",
                    },
                ),
            ),
        )
    fee_row, fee = fee_legs[0]
    fee_candidate = ParsedRow(
        sequence_number=sequence + 1,
        source_document_index=fee_row.document_index,
        source_row_number=fee_row.source_row_number,
        raw_data=fee_row.raw_data,
        normalized_candidate={
            **common,
            "source_operation_id": f"{transaction_id}:fee:{fee_row.values['Currency']}",
            "operation_type": "fee",
            "payload": {
                "instrument_reference": _asset_reference(fee_row.values["Currency"]),
                "quantity": _decimal_json(abs(fee)),
            },
        },
    )
    return trade, fee_candidate


def _parse_onchain_transfer(
    row: _SourceRow, funding_rows: tuple[_SourceRow, ...], sequence: int
) -> tuple[tuple[ParsedRow, ...], _SourceRow | None]:
    errors: list[Diagnostic] = []
    occurred_at = _timestamp(row.values["Date"], errors)
    amount = _number(row.values["Amount"], "Amount", errors)
    transfer_type = row.values["Type"].strip().casefold()
    if transfer_type not in {"withdraw", "deposit"}:
        errors.append(
            {"code": "bybit_transfer_type_invalid", "message": "Transfer type is unsupported"}
        )
    candidates: list[tuple[_SourceRow, Decimal]] = []
    if occurred_at is not None and amount is not None:
        transfer_time = datetime.fromisoformat(occurred_at.replace("Z", "+00:00"))
        for funding in funding_rows:
            if funding.values["Coin"] != row.values["Asset"]:
                continue
            funding_type = funding.values["Type"].strip().casefold()
            if funding_type != ("withdraw" if transfer_type == "withdraw" else "deposit"):
                continue
            funding_errors: list[Diagnostic] = []
            funding_time_json = _timestamp(funding.values["Date & Time(UTC)"], funding_errors)
            funding_amount = _number(funding.values["QTY"], "QTY", funding_errors)
            if funding_errors or funding_time_json is None or funding_amount is None:
                continue
            funding_time = datetime.fromisoformat(funding_time_json.replace("Z", "+00:00"))
            if abs(funding_time - transfer_time) <= timedelta(minutes=5):
                candidates.append((funding, funding_amount))
    if len(candidates) != 1:
        errors.append(
            {
                "code": "bybit_transfer_match_invalid",
                "message": "On-chain transfer must match exactly one Funding movement",
            }
        )
    if errors or occurred_at is None or amount is None:
        return (
            (
                ParsedRow(
                    sequence_number=sequence,
                    source_document_index=row.document_index,
                    source_row_number=row.source_row_number,
                    raw_data=row.raw_data,
                    errors=tuple(errors),
                ),
            ),
            None,
        )
    funding_row, funding_amount = candidates[0]
    if transfer_type == "withdraw" and not (funding_amount < 0 and abs(funding_amount) >= amount):
        errors.append(
            {
                "code": "bybit_withdrawal_amount_invalid",
                "message": "Funding withdrawal must cover the on-chain amount",
            }
        )
    if transfer_type == "deposit" and not (funding_amount > 0 and funding_amount == amount):
        errors.append(
            {
                "code": "bybit_deposit_amount_invalid",
                "message": "Funding deposit must equal the on-chain amount",
            }
        )
    if errors:
        return (
            (
                ParsedRow(
                    sequence_number=sequence,
                    source_document_index=row.document_index,
                    source_row_number=row.source_row_number,
                    raw_data=row.raw_data,
                    errors=tuple(errors),
                ),
            ),
            funding_row,
        )
    tx_id = row.values["Tx ID"]
    common = {"occurred_at": occurred_at, "time_precision": "second"}
    transfer_candidate = ParsedRow(
        sequence_number=sequence,
        source_document_index=row.document_index,
        source_row_number=row.source_row_number,
        raw_data={
            **row.raw_data,
            "linked_source_row": {
                "document_index": funding_row.document_index,
                "source_row_number": funding_row.source_row_number,
            },
        },
        normalized_candidate={
            **common,
            "source_operation_id": f"{tx_id}:transfer",
            "operation_type": "crypto_transfer",
            "payload": {
                "direction": "outbound" if transfer_type == "withdraw" else "inbound",
                "instrument_reference": _asset_reference(row.values["Asset"]),
                "quantity": _decimal_json(amount),
            },
        },
    )
    fee = abs(funding_amount) - amount if transfer_type == "withdraw" else Decimal(0)
    if fee == 0:
        return (transfer_candidate,), funding_row
    fee_candidate = ParsedRow(
        sequence_number=sequence + 1,
        source_document_index=funding_row.document_index,
        source_row_number=funding_row.source_row_number,
        raw_data=funding_row.raw_data,
        normalized_candidate={
            **common,
            "source_operation_id": f"{tx_id}:network-fee:{row.values['Asset']}",
            "operation_type": "fee",
            "payload": {
                "instrument_reference": _asset_reference(row.values["Asset"]),
                "quantity": _decimal_json(fee),
            },
        },
    )
    return (transfer_candidate, fee_candidate), funding_row


def _parse_funding_adjustment(row: _SourceRow, sequence: int) -> ParsedRow:
    errors: list[Diagnostic] = []
    occurred_at = _timestamp(row.values["Date & Time(UTC)"], errors)
    quantity = _number(row.values["QTY"], "QTY", errors)
    event_type = row.values["Type"].strip()
    if quantity == 0:
        errors.append(
            {
                "code": "bybit_funding_quantity_zero",
                "message": "Funding event quantity must not be zero",
            }
        )
    if errors or occurred_at is None or quantity is None or quantity == 0:
        return ParsedRow(
            sequence_number=sequence,
            source_document_index=row.document_index,
            source_row_number=row.source_row_number,
            raw_data=row.raw_data,
            errors=tuple(errors),
        )
    return ParsedRow(
        sequence_number=sequence,
        source_document_index=row.document_index,
        source_row_number=row.source_row_number,
        raw_data=row.raw_data,
        normalized_candidate={
            "occurred_at": occurred_at,
            "time_precision": "second",
            "operation_type": "balance_adjustment",
            "payload": {
                "instrument_reference": _asset_reference(row.values["Coin"]),
                "quantity_change": _decimal_json(quantity),
                "reason": f"Bybit funding event: {event_type}"[:1000],
            },
        },
    )


def _excluded(row: _SourceRow, sequence: int, code: str) -> ParsedRow:
    return ParsedRow(
        sequence_number=sequence,
        source_document_index=row.document_index,
        source_row_number=row.source_row_number,
        raw_data=row.raw_data,
        status=ImportRowStatus.EXCLUDED,
        warnings=({"code": code, "message": "Internal CEX movement was excluded"},),
    )


def _asset_reference(code: str) -> dict[str, object]:
    normalized = code.strip().upper()
    return {
        "crypto_asset_code": normalized,
        "auto_create": True,
        "name": normalized,
        "instrument_type": "crypto_asset",
        "currency": "USD",
    }


def _number(raw: str, field: str, errors: list[Diagnostic]) -> Decimal | None:
    if _DECIMAL.fullmatch(raw) is None:
        errors.append({"code": "bybit_decimal_invalid", "message": f"{field} is invalid"})
        return None
    try:
        return Decimal(raw)
    except InvalidOperation:
        errors.append({"code": "bybit_decimal_invalid", "message": f"{field} is invalid"})
        return None


def _decimal_json(value: Decimal) -> str:
    result = format(value, "f")
    if "." in result:
        result = result.rstrip("0").rstrip(".")
    return result


def _timestamp(raw: str, errors: list[Diagnostic]) -> str | None:
    try:
        value = datetime.strptime(raw, "%Y-%m-%d %H:%M:%S").replace(tzinfo=UTC)
    except ValueError:
        errors.append(
            {"code": "bybit_timestamp_invalid", "message": "UTC timestamp is invalid"}
        )
        return None
    return value.isoformat().replace("+00:00", "Z")


def _all_timestamps(tables: tuple[_Table, ...]) -> list[datetime]:
    result: list[datetime] = []
    timestamp_fields = {
        "spot_trade_history": "Timestamp (UTC+0)",
        "uta_asset_change_details": "Time(UTC)",
        "funding_asset_change_details": "Date & Time(UTC)",
        "withdraw_deposit_history": "Date",
    }
    for table in tables:
        field = timestamp_fields.get(table.role or "")
        if field is None:
            continue
        for row in table.rows:
            try:
                result.append(
                    datetime.strptime(row.values[field], "%Y-%m-%d %H:%M:%S").replace(
                        tzinfo=UTC
                    )
                )
            except ValueError:
                continue
    return result
