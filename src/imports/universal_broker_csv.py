from __future__ import annotations

import csv
import re
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal, InvalidOperation
from io import StringIO

from pydantic import TypeAdapter, ValidationError

from imports.adapters import (
    DetectionResult,
    ImportDocument,
    ParsedImport,
    ParsedRow,
    ValidationResult,
)
from imports.models import ImportCompleteness, ImportFileFormat
from shared.exact import CurrencyCode, Money, Price, Quantity, decimal_to_json

type Diagnostic = dict[str, object]

_HEADER_NORMALIZER = re.compile(r"[^a-z0-9]+")
_PLAIN_DECIMAL = re.compile(r"^[+-]?[0-9]+(?:\.[0-9]+)?$")
_ISIN = re.compile(r"^[A-Z]{2}[A-Z0-9]{9}[0-9]$")
_MARKET_CODE = re.compile(r"^[A-Z0-9][A-Z0-9._-]{0,31}$")
_DATE_ONLY = re.compile(r"^\d{4}-\d{2}-\d{2}$")
_LOCAL_DATE = re.compile(r"^\d{2}[./]\d{2}[./]\d{4}$")
_MINUTE_TIME = re.compile(r"[T ]\d{2}:\d{2}(?:Z|[+-]\d{2}:\d{2})$")
_FRACTION = re.compile(r"[.,](\d+)(?:Z|[+-]\d{2}:\d{2})$")
_REQUIRED_HEADERS = frozenset({"date", "type", "currency"})
_TYPE_ALIASES = {
    "buy": "buy",
    "sell": "sell",
    "dividend": "dividend",
    "coupon": "coupon",
    "fee": "fee",
    "tax": "tax",
    "cashin": "cash_in",
    "cashout": "cash_out",
}
_MONEY_ADAPTER: TypeAdapter[Decimal] = TypeAdapter(Money)
_QUANTITY_ADAPTER: TypeAdapter[Decimal] = TypeAdapter(Quantity)
_PRICE_ADAPTER: TypeAdapter[Decimal] = TypeAdapter(Price)
_CURRENCY_ADAPTER: TypeAdapter[str] = TypeAdapter(CurrencyCode)


@dataclass(frozen=True, slots=True)
class _CsvSourceRow:
    source_row_number: int
    raw_data: dict[str, object]
    values: dict[str, str]


@dataclass(frozen=True, slots=True)
class _CsvTable:
    headers: frozenset[str]
    rows: tuple[_CsvSourceRow, ...]
    diagnostics: tuple[Diagnostic, ...] = ()


class UniversalBrokerCsvAdapter:
    format_id = "universal_broker"
    version = "1.0"
    supported_file_formats = frozenset({ImportFileFormat.CSV})

    def detect(self, document: ImportDocument) -> DetectionResult:
        table = _read_table(document)
        completeness = ImportCompleteness.UNKNOWN
        return DetectionResult(
            matched=True,
            completeness=completeness,
            diagnostics=table.diagnostics,
        )

    def parse(self, document: ImportDocument) -> ParsedImport:
        table = _read_table(document)
        if table.diagnostics:
            return ParsedImport(
                rows=(
                    ParsedRow(
                        sequence_number=1,
                        source_row_number=1,
                        raw_data={},
                        errors=table.diagnostics,
                    ),
                )
            )

        parsed_rows: list[ParsedRow] = []
        sequence_number = 1
        for source_row in table.rows:
            rows = _parse_source_row(source_row, sequence_number)
            parsed_rows.extend(rows)
            sequence_number += len(rows)
        return ParsedImport(rows=tuple(parsed_rows))

    def validate(self, parsed: ParsedImport) -> ValidationResult:
        return ValidationResult(rows=parsed.rows, diagnostics=parsed.diagnostics)


def _read_table(document: ImportDocument) -> _CsvTable:
    document.stream.seek(0)
    payload = document.stream.read()
    document.stream.seek(0)
    try:
        text = payload.decode("utf-8-sig")
    except UnicodeDecodeError:
        return _table_error("csv_encoding_invalid", "CSV must use UTF-8 encoding")
    if not text.strip():
        return _table_error("csv_empty", "CSV does not contain a header row")

    sample = text[:65536]
    try:
        dialect = csv.Sniffer().sniff(sample, delimiters=",;\t|")
    except csv.Error:
        return _table_error("csv_delimiter_invalid", "CSV delimiter could not be detected")

    try:
        reader = csv.DictReader(StringIO(text, newline=""), dialect=dialect)
        if reader.fieldnames is None:
            return _table_error("csv_header_missing", "CSV does not contain a header row")
        normalized_headers = [_normalize_header(item) for item in reader.fieldnames]
        if any(not header for header in normalized_headers):
            return _table_error("csv_header_invalid", "CSV contains an empty header")
        if len(normalized_headers) != len(set(normalized_headers)):
            return _table_error(
                "csv_header_duplicate",
                "CSV contains duplicate normalized headers",
            )
        headers = frozenset(normalized_headers)
        missing = sorted(_REQUIRED_HEADERS - headers)
        if missing:
            return _table_error(
                "csv_required_headers_missing",
                "CSV must contain Date, Type, and Currency headers",
                fields=missing,
            )

        rows: list[_CsvSourceRow] = []
        for source_row_number, raw_row in enumerate(reader, start=2):
            if None in raw_row:
                return _table_error(
                    "csv_column_count_invalid",
                    "CSV row contains more values than the header",
                    source_row_number=source_row_number,
                )
            raw_data = {
                original: (raw_row.get(original) or "").strip()
                for original in reader.fieldnames
            }
            if not any(raw_data.values()):
                continue
            values = {
                normalized: raw_data[original]
                for original, normalized in zip(reader.fieldnames, normalized_headers, strict=True)
            }
            rows.append(
                _CsvSourceRow(
                    source_row_number=source_row_number,
                    raw_data=raw_data,
                    values=values,
                )
            )
    except csv.Error:
        return _table_error("csv_structure_invalid", "CSV structure is invalid")

    if not rows:
        return _table_error("csv_rows_missing", "CSV does not contain data rows")
    return _CsvTable(headers=headers, rows=tuple(rows))


def _table_error(code: str, message: str, **details: object) -> _CsvTable:
    diagnostic: Diagnostic = {"code": code, "message": message}
    diagnostic.update(details)
    return _CsvTable(headers=frozenset(), rows=(), diagnostics=(diagnostic,))


def _normalize_header(value: str) -> str:
    return _HEADER_NORMALIZER.sub("", value.strip().casefold())


def _parse_source_row(source: _CsvSourceRow, sequence_number: int) -> tuple[ParsedRow, ...]:
    errors: list[Diagnostic] = []
    warnings: list[Diagnostic] = []
    operation_type = _TYPE_ALIASES.get(_normalize_header(source.values.get("type", "")))
    if operation_type is None:
        errors.append(
            {
                "code": "operation_type_unsupported",
                "message": "Type must be Buy, Sell, Dividend, Coupon, Fee, Tax, CashIn, or CashOut",
            }
        )

    occurred_at, time_precision = _parse_timestamp(source.values.get("date", ""), errors)
    currency = _parse_currency(source.values.get("currency", ""), "Currency", errors)
    source_operation_id = _optional_text(
        source.values.get("externalid", ""),
        "ExternalId",
        256,
        errors,
    )
    note = _optional_text(source.values.get("note", ""), "Note", 1000, errors)
    reference = _instrument_reference(source.values, currency, errors)
    accrued_interest = _optional_decimal(
        source.values.get("accruedinterest", ""),
        _MONEY_ADAPTER,
        "AccruedInterest",
        errors,
    )
    if accrued_interest is not None and accrued_interest != 0:
        warnings.append(
            {
                "code": "accrued_interest_not_normalized",
                "message": "AccruedInterest requires explicit review and was not normalized",
            }
        )

    if errors or operation_type is None or occurred_at is None or time_precision is None:
        return (_error_row(source, sequence_number, errors, warnings),)

    common: dict[str, object] = {
        "occurred_at": occurred_at,
        "time_precision": time_precision,
    }
    if source_operation_id is not None:
        common["source_operation_id"] = source_operation_id
    if note is not None:
        common["note"] = note

    candidates: list[tuple[dict[str, object], list[Diagnostic], list[Diagnostic]]] = []
    primary_errors: list[Diagnostic] = []
    primary = _primary_candidate(
        operation_type,
        source.values,
        currency,
        reference,
        common,
        primary_errors,
    )
    if primary is not None:
        candidates.append((primary, list(warnings), primary_errors))
    else:
        return (_error_row(source, sequence_number, primary_errors, warnings),)

    if operation_type not in {"fee", "tax"}:
        fee_errors: list[Diagnostic] = []
        fee = _component_candidate(
            "fee",
            source.values.get("fee", ""),
            source.values.get("feecurrency", ""),
            currency,
            reference,
            common,
            fee_errors,
        )
        if fee is not None or fee_errors:
            candidates.append((fee or {}, [], fee_errors))

        tax_errors: list[Diagnostic] = []
        tax_currency = source.values.get("taxcurrency", "") or currency or ""
        tax = _component_candidate(
            "tax",
            source.values.get("tax", ""),
            tax_currency,
            currency,
            reference,
            common,
            tax_errors,
        )
        if tax is not None or tax_errors:
            candidates.append((tax or {}, [], tax_errors))

    rows: list[ParsedRow] = []
    for offset, (candidate, candidate_warnings, candidate_errors) in enumerate(candidates):
        rows.append(
            ParsedRow(
                sequence_number=sequence_number + offset,
                source_row_number=source.source_row_number,
                raw_data=source.raw_data,
                normalized_candidate=candidate or None,
                warnings=tuple(candidate_warnings),
                errors=tuple(candidate_errors),
            )
        )
    return tuple(rows)


def _primary_candidate(
    operation_type: str,
    values: dict[str, str],
    currency: str | None,
    reference: dict[str, object] | None,
    common: dict[str, object],
    errors: list[Diagnostic],
) -> dict[str, object] | None:
    payload: dict[str, object]
    canonical_type: str
    if operation_type in {"buy", "sell"}:
        quantity = _required_decimal(
            values.get("quantity", ""),
            _QUANTITY_ADAPTER,
            "Quantity",
            errors,
        )
        price = _required_decimal(values.get("price", ""), _PRICE_ADAPTER, "Price", errors)
        if reference is None:
            errors.append(
                {
                    "code": "instrument_reference_required",
                    "message": "Trade rows require ISIN or Exchange and Symbol",
                }
            )
        canonical_type = "trade"
        payload = {
            "side": operation_type,
            "quantity": quantity,
            "price": price,
            "price_currency": currency,
            "instrument_reference": reference,
        }
    elif operation_type in {"dividend", "coupon"}:
        amount = _required_decimal(values.get("amount", ""), _MONEY_ADAPTER, "Amount", errors)
        canonical_type = "income"
        payload = {
            "income_type": operation_type,
            "amount": amount,
            "currency": currency,
        }
        if reference is not None:
            payload["instrument_reference"] = reference
    elif operation_type in {"fee", "tax"}:
        field_name = "Fee" if operation_type == "fee" else "Tax"
        raw_amount = values.get("amount", "") or values.get(operation_type, "")
        amount = _required_decimal(raw_amount, _MONEY_ADAPTER, field_name, errors)
        canonical_type = operation_type
        payload = {"amount": amount, "currency": currency}
        if reference is not None:
            payload["instrument_reference"] = reference
    else:
        amount = _required_decimal(values.get("amount", ""), _MONEY_ADAPTER, "Amount", errors)
        canonical_type = "cash_movement"
        payload = {
            "direction": "deposit" if operation_type == "cash_in" else "withdrawal",
            "amount": amount,
            "currency": currency,
        }

    if errors or currency is None:
        return None
    return {**common, "operation_type": canonical_type, "payload": payload}


def _component_candidate(
    operation_type: str,
    raw_amount: str,
    raw_currency: str,
    default_currency: str | None,
    reference: dict[str, object] | None,
    common: dict[str, object],
    errors: list[Diagnostic],
) -> dict[str, object] | None:
    amount = _optional_decimal(raw_amount, _MONEY_ADAPTER, operation_type.title(), errors)
    if amount is None or amount == 0:
        return None
    currency = _parse_currency(raw_currency, f"{operation_type.title()}Currency", errors)
    if currency is None and not raw_currency and default_currency is not None:
        currency = default_currency
    if errors or currency is None:
        return None
    payload: dict[str, object] = {
        "amount": decimal_to_json(abs(amount)),
        "currency": currency,
    }
    if reference is not None:
        payload["instrument_reference"] = reference
    return {**common, "operation_type": operation_type, "payload": payload}


def _instrument_reference(
    values: dict[str, str],
    currency: str | None,
    errors: list[Diagnostic],
) -> dict[str, object] | None:
    isin = values.get("isin", "").strip().upper()
    ticker = values.get("symbol", "").strip().upper()
    exchange = values.get("exchange", "").strip().upper()
    if not isin and not ticker and not exchange:
        return None
    if isin and _ISIN.fullmatch(isin) is None:
        errors.append({"code": "isin_invalid", "message": "ISIN has an invalid format"})
    if bool(ticker) != bool(exchange):
        errors.append(
            {
                "code": "ticker_exchange_incomplete",
                "message": "Symbol and Exchange must be supplied together",
            }
        )
    if ticker and _MARKET_CODE.fullmatch(ticker) is None:
        errors.append({"code": "ticker_invalid", "message": "Symbol has an invalid format"})
    if exchange and _MARKET_CODE.fullmatch(exchange) is None:
        errors.append(
            {"code": "exchange_invalid", "message": "Exchange has an invalid format"}
        )
    if errors or currency is None:
        return None
    reference: dict[str, object] = {"currency": currency}
    if isin:
        reference["isin"] = isin
    if ticker:
        reference["ticker"] = ticker
        reference["exchange"] = exchange
    return reference


def _parse_timestamp(
    raw_value: str,
    errors: list[Diagnostic],
) -> tuple[str | None, str | None]:
    value = raw_value.strip()
    if not value:
        errors.append({"code": "date_required", "message": "Date is required"})
        return None, None
    try:
        if _DATE_ONLY.fullmatch(value):
            parsed = datetime.strptime(value, "%Y-%m-%d").replace(tzinfo=UTC)
            return _datetime_json(parsed), "date"
        if _LOCAL_DATE.fullmatch(value):
            separator = "." if "." in value else "/"
            parsed = datetime.strptime(value, f"%d{separator}%m{separator}%Y").replace(tzinfo=UTC)
            return _datetime_json(parsed), "date"
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        if parsed.tzinfo is None or parsed.utcoffset() is None:
            raise ValueError
    except ValueError:
        errors.append(
            {
                "code": "date_invalid",
                "message": "Date must be a date or timezone-aware ISO 8601 timestamp",
            }
        )
        return None, None

    fraction = _FRACTION.search(value)
    if fraction is not None:
        precision = "millisecond" if len(fraction.group(1)) <= 3 else "microsecond"
    elif _MINUTE_TIME.search(value) is not None:
        precision = "minute"
    else:
        precision = "second"
    return _datetime_json(parsed.astimezone(UTC)), precision


def _datetime_json(value: datetime) -> str:
    return value.astimezone(UTC).isoformat().replace("+00:00", "Z")


def _parse_currency(
    raw_value: str,
    field_name: str,
    errors: list[Diagnostic],
) -> str | None:
    value = raw_value.strip().upper()
    try:
        return _CURRENCY_ADAPTER.validate_python(value)
    except ValidationError:
        errors.append(
            {
                "code": "currency_invalid",
                "message": f"{field_name} must contain three uppercase letters",
            }
        )
        return None


def _required_decimal(
    raw_value: str,
    adapter: TypeAdapter[Decimal],
    field_name: str,
    errors: list[Diagnostic],
) -> str | None:
    parsed = _optional_decimal(raw_value, adapter, field_name, errors)
    if parsed is None:
        if not raw_value.strip():
            errors.append(
                {"code": "value_required", "message": f"{field_name} is required"}
            )
        return None
    if parsed == 0:
        errors.append(
            {"code": "value_not_positive", "message": f"{field_name} must not be zero"}
        )
        return None
    return decimal_to_json(abs(parsed))


def _optional_decimal(
    raw_value: str,
    adapter: TypeAdapter[Decimal],
    field_name: str,
    errors: list[Diagnostic],
) -> Decimal | None:
    value = raw_value.strip()
    if not value:
        return None
    normalized = _normalize_decimal(value)
    if normalized is None:
        errors.append(
            {
                "code": "decimal_format_invalid",
                "message": f"{field_name} has an invalid decimal format",
            }
        )
        return None
    try:
        decimal_value = Decimal(normalized)
        return adapter.validate_python(decimal_to_json(abs(decimal_value)))
    except (InvalidOperation, ValidationError):
        errors.append(
            {
                "code": "decimal_value_invalid",
                "message": f"{field_name} exceeds the supported precision or range",
            }
        )
        return None


def _normalize_decimal(raw_value: str) -> str | None:
    value = (
        raw_value.replace("\u00a0", "")
        .replace("\u202f", "")
        .replace(" ", "")
        .replace("'", "")
    )
    comma_count = value.count(",")
    dot_count = value.count(".")
    if comma_count and dot_count:
        decimal_separator = "," if value.rfind(",") > value.rfind(".") else "."
        thousands_separator = "." if decimal_separator == "," else ","
        value = value.replace(thousands_separator, "")
        if decimal_separator == ",":
            value = value.replace(",", ".")
    elif comma_count == 1:
        value = value.replace(",", ".")
    elif comma_count > 1:
        groups = value.lstrip("+-").split(",")
        if not groups[0] or any(len(group) != 3 for group in groups[1:]):
            return None
        value = value.replace(",", "")
    elif dot_count > 1:
        groups = value.lstrip("+-").split(".")
        if not groups[0] or any(len(group) != 3 for group in groups[1:]):
            return None
        value = value.replace(".", "")
    if _PLAIN_DECIMAL.fullmatch(value) is None:
        return None
    return value.lstrip("+")


def _optional_text(
    raw_value: str,
    field_name: str,
    max_length: int,
    errors: list[Diagnostic],
) -> str | None:
    value = raw_value.strip()
    if not value:
        return None
    if len(value) > max_length:
        errors.append(
            {
                "code": "text_too_long",
                "message": f"{field_name} exceeds {max_length} characters",
            }
        )
        return None
    return value


def _error_row(
    source: _CsvSourceRow,
    sequence_number: int,
    errors: list[Diagnostic],
    warnings: list[Diagnostic],
) -> ParsedRow:
    return ParsedRow(
        sequence_number=sequence_number,
        source_row_number=source.source_row_number,
        raw_data=source.raw_data,
        warnings=tuple(warnings),
        errors=tuple(errors),
    )
