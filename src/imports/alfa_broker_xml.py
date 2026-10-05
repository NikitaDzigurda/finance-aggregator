from __future__ import annotations

import re
from collections import Counter
from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal, InvalidOperation
from xml.etree import ElementTree
from zoneinfo import ZoneInfo

from pydantic import TypeAdapter, ValidationError

from imports.adapters import (
    DetectionResult,
    ImportDocument,
    ParsedImport,
    ParsedRow,
    ValidationResult,
)
from imports.models import ImportCompleteness, ImportFileFormat, ImportRowStatus
from imports.xml_security import XmlSecurityError, XmlSecurityLimits, validate_xml_document
from shared.config import get_settings
from shared.exact import CurrencyCode, Money, Price, Quantity, decimal_to_json

type Diagnostic = dict[str, object]

_TIMEZONE = ZoneInfo("Europe/Moscow")
_ISIN = re.compile(r"^[A-Z]{2}[A-Z0-9]{9}[0-9]$")
_REQUIRED_METADATA = frozenset(
    {"sys_name", "full_name", "namebroker", "treaty", "date_start", "date_end"}
)
_REQUIRED_COLLECTIONS = frozenset(
    {"positions", "trades_finished", "trades_unfinished", "money_moves", "transfers"}
)
_MONEY_ADAPTER: TypeAdapter[Decimal] = TypeAdapter(Money)
_QUANTITY_ADAPTER: TypeAdapter[Decimal] = TypeAdapter(Quantity)
_PRICE_ADAPTER: TypeAdapter[Decimal] = TypeAdapter(Price)
_CURRENCY_ADAPTER: TypeAdapter[str] = TypeAdapter(CurrencyCode)


@dataclass(frozen=True, slots=True)
class _InstrumentReference:
    isin: str | None
    provider_code: str | None
    name: str | None = None
    instrument_type: str | None = None


@dataclass(frozen=True, slots=True)
class _XmlLayout:
    matched: bool
    root: ElementTree.Element | None = None
    reporting_period_start: date | None = None
    reporting_period_end: date | None = None
    diagnostics: tuple[Diagnostic, ...] = ()


class AlfaBrokerXmlAdapter:
    format_id = "alfa_broker_xml_import"
    version = "1.0"
    supported_file_formats = frozenset({ImportFileFormat.XML})

    def detect(self, document: ImportDocument) -> DetectionResult:
        layout = _read_layout(document)
        return DetectionResult(
            matched=layout.matched,
            completeness=(
                ImportCompleteness.PERIOD_LEDGER
                if layout.matched
                else ImportCompleteness.UNKNOWN
            ),
            diagnostics=layout.diagnostics,
            reporting_period_start=layout.reporting_period_start,
            reporting_period_end=layout.reporting_period_end,
        )

    def parse(self, document: ImportDocument) -> ParsedImport:
        layout = _read_layout(document)
        if not layout.matched or layout.root is None:
            return ParsedImport(
                rows=(
                    ParsedRow(
                        sequence_number=1,
                        raw_data={},
                        errors=layout.diagnostics
                        or (
                            {
                                "code": "alfa_xml_signature_invalid",
                                "message": "XML does not match Alfa broker import v1",
                            },
                        ),
                    ),
                )
            )

        root = layout.root
        positions = _children(_required_child(root, "positions"), "position")
        references_by_name, references_by_isin = _position_references(positions)
        finished = _children(_required_child(root, "trades_finished"), "trade")
        unfinished = _children(_required_child(root, "trades_unfinished"), "trade")
        money_moves = _children(_required_child(root, "money_moves"), "money_move")
        transfers = _children(_required_child(root, "transfers"), "transfer")

        trade_numbers = [_text(_child_text(item, "trade_no")) for item in finished]
        duplicate_trade_numbers = {
            value for value, count in Counter(trade_numbers).items() if value and count > 1
        }
        finished_trade_numbers = {value for value in trade_numbers if value}

        rows: list[ParsedRow] = []
        sequence_number = 1
        for trade in finished:
            parsed = _parse_finished_trade(
                trade,
                sequence_number=sequence_number,
                references_by_name=references_by_name,
                references_by_isin=references_by_isin,
                duplicate_trade_numbers=duplicate_trade_numbers,
            )
            rows.extend(parsed)
            sequence_number += len(parsed)

        for trade in unfinished:
            rows.append(
                _excluded_row(
                    trade,
                    sequence_number=sequence_number,
                    source_section="trades_unfinished",
                    code="alfa_unfinished_trade_information",
                    message="Unfinished trade is informational and cannot be confirmed",
                )
            )
            sequence_number += 1

        for index, money_move in enumerate(money_moves, start=1):
            rows.append(
                _parse_money_move(
                    money_move,
                    sequence_number=sequence_number,
                    source_index=index,
                    finished_trade_numbers=finished_trade_numbers,
                )
            )
            sequence_number += 1

        for position in positions:
            rows.append(_parse_position_control(position, sequence_number))
            sequence_number += 1

        money_totals = _child(root, "money_moves_total")
        if money_totals is not None:
            rows.append(_parse_money_totals_control(money_totals, sequence_number))
            sequence_number += 1

        for transfer in transfers:
            rows.append(
                _parse_security_transfer(
                    transfer,
                    sequence_number=sequence_number,
                    references_by_name=references_by_name,
                    references_by_isin=references_by_isin,
                )
            )
            sequence_number += 1

        return ParsedImport(rows=tuple(rows), diagnostics=layout.diagnostics)

    def validate(self, parsed: ParsedImport) -> ValidationResult:
        return ValidationResult(rows=parsed.rows, diagnostics=parsed.diagnostics)


def _read_layout(document: ImportDocument) -> _XmlLayout:
    settings = get_settings()
    try:
        metadata = validate_xml_document(
            document.stream,
            limits=XmlSecurityLimits(
                max_size_bytes=settings.import_max_file_size_bytes,
                max_depth=settings.import_xml_max_depth,
                max_elements=settings.import_xml_max_elements,
                max_value_length=settings.import_xml_max_value_length,
            ),
        )
        if metadata.root_element == "Report":
            document.stream.seek(0)
            standard_root = ElementTree.parse(document.stream).getroot()
            if standard_root.attrib.get("Name") == "MyBroker":
                return _layout_error(
                    "alfa_xml_variant_unsupported",
                    (
                        "Alfa standard XML report is not the XML variant intended "
                        "for import"
                    ),
                )
        if metadata.root_element != "report_broker":
            return _layout_error(
                "alfa_xml_root_invalid",
                "XML does not contain the required broker report root",
            )
        document.stream.seek(0)
        root = ElementTree.parse(document.stream).getroot()
    except XmlSecurityError as exc:
        return _layout_error(exc.code, exc.message)
    except (ElementTree.ParseError, OSError, ValueError):
        return _layout_error("import_xml_malformed", "Uploaded XML is malformed")
    finally:
        document.stream.seek(0)

    direct_children = {_local_name(item.tag) for item in root}
    if not _REQUIRED_METADATA.issubset(direct_children):
        return _layout_error(
            "alfa_xml_metadata_missing",
            "XML is missing required Alfa broker report metadata",
        )
    if not _REQUIRED_COLLECTIONS.issubset(direct_children):
        return _layout_error(
            "alfa_xml_collection_missing",
            "XML is missing a required Alfa broker report collection",
        )
    try:
        period_start = _report_date(_text(_child_text(root, "date_start")))
        period_end = _report_date(_text(_child_text(root, "date_end")))
        if period_start > period_end:
            raise ValueError
    except ValueError:
        return _layout_error(
            "alfa_xml_reporting_period_invalid",
            "XML contains an invalid reporting period",
        )

    diagnostics: list[Diagnostic] = [
        {
            "code": "alfa_positions_reconciliation_available",
            "message": "Positions were read as reconciliation data only",
            "count": len(_children(_required_child(root, "positions"), "position")),
        }
    ]
    if _child(root, "money_moves_total") is None:
        diagnostics.append(
            {
                "code": "alfa_money_totals_missing",
                "message": "Report does not contain optional money reconciliation totals",
            }
        )
    else:
        diagnostics.append(
            {
                "code": "alfa_money_totals_reconciliation_available",
                "message": "Money totals were read as reconciliation data only",
            }
        )
    return _XmlLayout(
        matched=True,
        root=root,
        reporting_period_start=period_start,
        reporting_period_end=period_end,
        diagnostics=tuple(diagnostics),
    )


def _position_references(
    positions: tuple[ElementTree.Element, ...],
) -> tuple[dict[str, _InstrumentReference], dict[str, _InstrumentReference]]:
    by_name_candidates: dict[str, list[_InstrumentReference]] = {}
    by_isin: dict[str, _InstrumentReference] = {}
    for position in positions:
        isin = _text(_child_text(position, "ISIN")).upper() or None
        provider_code = (
            _text(_child_text(position, "p_code"))
            or _text(_child_text(position, "act_id"))
            or None
        )
        reference = _InstrumentReference(
            isin=isin if isin is not None and _ISIN.fullmatch(isin) else None,
            provider_code=provider_code,
            name=_text(_child_text(position, "active_name")) or None,
            instrument_type=_alfa_instrument_type(
                _text(_child_text(position, "active_type"))
            ),
        )
        name = _text(_child_text(position, "active_name"))
        if name:
            by_name_candidates.setdefault(name, []).append(reference)
        if isin:
            by_isin[isin] = reference
    by_name = {
        name: items[0] for name, items in by_name_candidates.items() if len(items) == 1
    }
    return by_name, by_isin


def _parse_finished_trade(
    trade: ElementTree.Element,
    *,
    sequence_number: int,
    references_by_name: dict[str, _InstrumentReference],
    references_by_isin: dict[str, _InstrumentReference],
    duplicate_trade_numbers: set[str],
) -> tuple[ParsedRow, ...]:
    errors: list[Diagnostic] = []
    trade_number = _text(_child_text(trade, "trade_no"))
    if not trade_number or len(trade_number) > 256:
        errors.append(
            {"code": "alfa_trade_number_invalid", "message": "Trade number is required"}
        )
    elif trade_number in duplicate_trade_numbers:
        errors.append(
            {
                "code": "alfa_trade_number_duplicate",
                "message": "Completed trades contain a duplicate trade number",
            }
        )

    occurred_at, time_precision = _datetime(
        _child_text(trade, "db_time"), "trade", errors
    )
    quantity = _decimal(
        _child_text(trade, "qty"), _QUANTITY_ADAPTER, "quantity", errors
    )
    price = _decimal(_child_text(trade, "Price"), _PRICE_ADAPTER, "price", errors)
    currency = _currency(_child_text(trade, "curr_calc"), errors)
    fee = _optional_decimal(
        _child_text(trade, "bank_tax"), _MONEY_ADAPTER, "fee", errors
    )
    if quantity == 0:
        errors.append(
            {"code": "alfa_trade_quantity_zero", "message": "Trade quantity must not be zero"}
        )
    if price is not None and price <= 0:
        errors.append(
            {"code": "alfa_trade_price_invalid", "message": "Trade price must be positive"}
        )

    reference = _trade_reference(
        trade,
        currency=currency,
        references_by_name=references_by_name,
        references_by_isin=references_by_isin,
    )
    if reference is None:
        errors.append(
            {
                "code": "alfa_instrument_reference_missing",
                "message": "Trade does not contain a supported instrument identifier",
            }
        )

    raw_data = _raw_data(trade)
    if (
        errors
        or occurred_at is None
        or time_precision is None
        or quantity is None
        or quantity == 0
        or price is None
        or price <= 0
        or currency is None
        or reference is None
    ):
        return (
            ParsedRow(
                sequence_number=sequence_number,
                source_sheet="trades_finished",
                raw_data=raw_data,
                errors=tuple(errors),
            ),
        )

    common: dict[str, object] = {
        "occurred_at": occurred_at,
        "time_precision": time_precision,
    }
    candidates: list[dict[str, object]] = [
        {
            **common,
            "source_operation_id": trade_number,
            "operation_type": "trade",
            "payload": {
                "side": "buy" if quantity > 0 else "sell",
                "quantity": decimal_to_json(abs(quantity)),
                "price": decimal_to_json(price),
                "price_currency": currency,
                "instrument_reference": reference,
            },
        }
    ]
    if fee is not None and fee != 0:
        candidates.append(
            {
                **common,
                "source_operation_id": trade_number,
                "operation_type": "fee",
                "payload": {
                    "amount": decimal_to_json(abs(fee)),
                    "currency": currency,
                    "instrument_reference": reference,
                },
            }
        )
    return tuple(
        ParsedRow(
            sequence_number=sequence_number + offset,
            source_sheet="trades_finished",
            raw_data=raw_data,
            normalized_candidate=candidate,
            reconciliation_data=(
                {
                    "position_effects": [
                        {
                            "key": _instrument_key(reference),
                            "change": decimal_to_json(quantity),
                        }
                    ]
                }
                if offset == 0
                else None
            ),
        )
        for offset, candidate in enumerate(candidates)
    )


def _trade_reference(
    trade: ElementTree.Element,
    *,
    currency: str | None,
    references_by_name: dict[str, _InstrumentReference],
    references_by_isin: dict[str, _InstrumentReference],
) -> dict[str, object] | None:
    raw_isin = _text(_child_text(trade, "isin_reg")).upper()
    position_reference = references_by_isin.get(raw_isin)
    if position_reference is None:
        position_reference = references_by_name.get(_text(_child_text(trade, "p_name")))
    isin = raw_isin if _ISIN.fullmatch(raw_isin) else None
    provider_code = position_reference.provider_code if position_reference is not None else None
    trade_name = _text(_child_text(trade, "p_name"))
    if isin is None and provider_code is None:
        return None
    result: dict[str, object] = {"provider": AlfaBrokerXmlAdapter.format_id}
    if currency is not None:
        result["currency"] = currency
    if isin is not None:
        result["isin"] = isin
    if provider_code is not None:
        result["provider_code"] = provider_code
    metadata_name = (
        position_reference.name
        if position_reference is not None and position_reference.name is not None
        else trade_name or None
    )
    metadata_type = (
        position_reference.instrument_type
        if position_reference is not None
        and position_reference.instrument_type is not None
        else _infer_alfa_type(trade_name)
    )
    if metadata_name is not None and metadata_type is not None and currency is not None:
        result.update(
            {
                "auto_create": True,
                "name": metadata_name[:200],
                "instrument_type": metadata_type,
            }
        )
    return result


def _instrument_key(reference: dict[str, object]) -> str:
    isin = reference.get("isin")
    if isinstance(isin, str):
        return f"isin:{isin}"
    return f"provider_code:{reference['provider_code']}"


def _parse_security_transfer(
    transfer: ElementTree.Element,
    *,
    sequence_number: int,
    references_by_name: dict[str, _InstrumentReference],
    references_by_isin: dict[str, _InstrumentReference],
) -> ParsedRow:
    errors: list[Diagnostic] = []
    occurred_at, time_precision = _datetime(
        _child_text(transfer, "settlement_date"), "security transfer", errors
    )
    quantity = _decimal(
        _child_text(transfer, "qty"), _QUANTITY_ADAPTER, "transfer quantity", errors
    )
    raw_isin = _text(_child_text(transfer, "ISIN")).upper()
    name = _text(_child_text(transfer, "p_name"))
    position_reference = references_by_isin.get(raw_isin)
    if position_reference is None:
        position_reference = references_by_name.get(name)
    isin = raw_isin if _ISIN.fullmatch(raw_isin) else None
    provider_code = position_reference.provider_code if position_reference else None
    if isin is None and provider_code is None:
        errors.append(
            {
                "code": "alfa_instrument_reference_missing",
                "message": "Transfer does not contain a supported instrument identifier",
            }
        )
    if quantity == 0:
        errors.append(
            {
                "code": "alfa_transfer_quantity_zero",
                "message": "Security transfer quantity must not be zero",
            }
        )
    raw_data = _raw_data(transfer)
    if (
        errors
        or occurred_at is None
        or time_precision is None
        or quantity is None
        or quantity == 0
    ):
        return ParsedRow(
            sequence_number=sequence_number,
            source_sheet="transfers",
            raw_data=raw_data,
            errors=tuple(errors),
        )
    reference: dict[str, object] = {"provider": AlfaBrokerXmlAdapter.format_id}
    if isin is not None:
        reference["isin"] = isin
    if provider_code is not None:
        reference["provider_code"] = provider_code
    if (
        position_reference is not None
        and position_reference.name is not None
        and position_reference.instrument_type is not None
    ):
        reference.update(
            {
                "auto_create": True,
                "name": position_reference.name[:200],
                "instrument_type": position_reference.instrument_type,
                "currency": "RUB",
            }
        )
    return ParsedRow(
        sequence_number=sequence_number,
        source_sheet="transfers",
        raw_data=raw_data,
        normalized_candidate={
            "occurred_at": occurred_at,
            "time_precision": time_precision,
            "operation_type": "balance_adjustment",
            "payload": {
                "instrument_reference": reference,
                "quantity_change": decimal_to_json(quantity),
                "reason": "Alfa-Investments reported securities transfer",
            },
        },
        reconciliation_data={
            "position_effects": [
                {"key": _instrument_key(reference), "change": decimal_to_json(quantity)}
            ]
        },
    )


def _alfa_instrument_type(value: str) -> str | None:
    normalized = " ".join(value.casefold().split())
    return {
        "акция": "stock",
        "акции": "stock",
        "облигация": "bond",
        "облигации": "bond",
        "биржевой фонд": "fund",
        "фонд": "fund",
        "фонды": "fund",
        "пай": "fund",
        "паи": "fund",
        "прочее": "fund",
        "валюта": "currency",
        "опцион": "option",
        "опционы": "option",
    }.get(normalized)


def _infer_alfa_type(name: str) -> str | None:
    normalized = name.casefold()
    if not normalized:
        return None
    if "облигац" in normalized or "офз" in normalized or "bond" in normalized:
        return "bond"
    if "etf" in normalized:
        return "etf"
    if any(
        marker in normalized
        for marker in (
            "бпиф",
            "опиф",
            "пиф",
            "фонд",
            "fund",
            "денежн",
            "накопительн",
        )
    ):
        return "fund"
    if "опцион" in normalized:
        return "option"
    return "stock"


def _report_date(value: str) -> date:
    for pattern in ("%Y-%m-%d", "%d.%m.%Y %H:%M:%S", "%d.%m.%Y"):
        try:
            return datetime.strptime(value, pattern).date()
        except ValueError:
            continue
    raise ValueError


def _parse_position_control(
    position: ElementTree.Element,
    sequence_number: int,
) -> ParsedRow:
    errors: list[Diagnostic] = []
    raw_isin = _text(_child_text(position, "ISIN")).upper()
    provider_code = (
        _text(_child_text(position, "p_code"))
        or _text(_child_text(position, "act_id"))
    )
    if _ISIN.fullmatch(raw_isin):
        key = f"isin:{raw_isin}"
    elif provider_code:
        key = f"provider_code:{provider_code}"
    else:
        key = ""
        errors.append(
            {
                "code": "alfa_position_identifier_missing",
                "message": "Position control has no supported instrument identifier",
            }
        )
    opening = _decimal(
        _child_text(position, "income_rest"),
        _QUANTITY_ADAPTER,
        "opening position",
        errors,
    )
    closing = _decimal(
        _child_text(position, "real_rest"),
        _QUANTITY_ADAPTER,
        "closing position",
        errors,
    )
    control = (
        None
        if errors or opening is None or closing is None
        else {
            "kind": "position",
            "key": key,
            "opening": decimal_to_json(opening),
            "closing": decimal_to_json(closing),
        }
    )
    return ParsedRow(
        sequence_number=sequence_number,
        source_sheet="positions",
        raw_data=_raw_data(position),
        reconciliation_data={"controls": [control]} if control is not None else None,
        status=ImportRowStatus.ERROR if errors else ImportRowStatus.EXCLUDED,
        warnings=(
            ()
            if errors
            else (
                {
                    "code": "alfa_position_reconciliation_only",
                    "message": "Position is reconciliation data only",
                },
            )
        ),
        errors=tuple(errors),
    )


def _parse_money_totals_control(
    totals: ElementTree.Element,
    sequence_number: int,
) -> ParsedRow:
    errors: list[Diagnostic] = []
    opening = _money_total_values(
        totals,
        group_name="begin_real_rest_total",
        item_name="begin_real_rest",
        field="opening cash",
        errors=errors,
    )
    closing = _money_total_values(
        totals,
        group_name="end_real_rest_total",
        item_name="end_real_rest",
        field="closing cash",
        errors=errors,
    )
    if set(opening) != set(closing):
        errors.append(
            {
                "code": "alfa_money_total_currency_mismatch",
                "message": "Opening and closing cash controls use different currencies",
            }
        )
    controls = [
        {
            "kind": "cash",
            "key": currency,
            "opening": decimal_to_json(opening[currency]),
            "closing": decimal_to_json(closing[currency]),
        }
        for currency in sorted(set(opening) & set(closing))
    ]
    empty_controls = not controls and not errors
    return ParsedRow(
        sequence_number=sequence_number,
        source_sheet="money_moves_total",
        raw_data=_raw_data(totals),
        reconciliation_data={"controls": controls} if not errors else None,
        status=ImportRowStatus.ERROR if errors else ImportRowStatus.EXCLUDED,
        warnings=(
            ()
            if errors
            else (
                {
                    "code": (
                        "alfa_money_totals_empty"
                        if empty_controls
                        else "alfa_money_totals_reconciliation_only"
                    ),
                    "message": (
                        "Money reconciliation totals are empty"
                        if empty_controls
                        else "Money totals are reconciliation data only"
                    ),
                },
            )
        ),
        errors=tuple(errors),
    )


def _money_total_values(
    totals: ElementTree.Element,
    *,
    group_name: str,
    item_name: str,
    field: str,
    errors: list[Diagnostic],
) -> dict[str, Decimal]:
    group = _child(totals, group_name)
    if group is None:
        errors.append(
            {"code": "alfa_money_total_missing", "message": f"Reported {field} is missing"}
        )
        return {}
    result: dict[str, Decimal] = {}
    for item in _children(group, item_name):
        raw_currency = _text(_child_text(item, "p_code"))
        if raw_currency.casefold() == "money":
            continue
        currency = _currency(raw_currency, errors)
        value = _decimal(_child_text(item, "value"), _MONEY_ADAPTER, field, errors)
        if currency is None or value is None:
            continue
        if currency in result:
            errors.append(
                {
                    "code": "alfa_money_total_duplicate_currency",
                    "message": "Cash reconciliation contains a duplicate currency",
                }
            )
            continue
        result[currency] = value
    return result


def _parse_money_move(
    money_move: ElementTree.Element,
    *,
    sequence_number: int,
    source_index: int,
    finished_trade_numbers: set[str],
) -> ParsedRow:
    trade_number = _text(_child_text(money_move, "trd_no"))
    if trade_number:
        if trade_number not in finished_trade_numbers:
            return ParsedRow(
                sequence_number=sequence_number,
                source_sheet="money_moves",
                source_row_number=source_index,
                raw_data=_raw_data(money_move),
                errors=(
                    {
                        "code": "alfa_trade_money_link_missing",
                        "message": "Trade-linked money movement has no completed trade",
                    },
                ),
            )
        return _excluded_row(
            money_move,
            sequence_number=sequence_number,
            source_section="money_moves",
            source_index=source_index,
            code="alfa_trade_money_reconciliation_only",
            message="Trade-linked money movement is reconciliation data only",
        )

    errors: list[Diagnostic] = []
    occurred_at, time_precision = _datetime(
        _child_text(money_move, "settlement_date"), "money movement", errors
    )
    amount = _decimal(
        _child_text(money_move, "volume"), _MONEY_ADAPTER, "money movement amount", errors
    )
    currency = _currency(_child_text(money_move, "p_code"), errors)
    operation_group = _text(_child_text(money_move, "oper_group")).casefold()
    operation_kind = _text(_child_text(money_move, "oper_type")).casefold()
    classification = _classify_money_move(operation_group, operation_kind)
    if classification is None:
        if not operation_group and operation_kind == "перевод":
            return _excluded_row(
                money_move,
                sequence_number=sequence_number,
                source_section="money_moves",
                source_index=source_index,
                code="alfa_internal_cash_transfer_ignored",
                message="Internal broker cash transfer is reconciliation data only",
            )
        return ParsedRow(
            sequence_number=sequence_number,
            source_sheet="money_moves",
            source_row_number=source_index,
            raw_data=_raw_data(money_move),
            warnings=(
                {
                    "code": "alfa_money_move_review_required",
                    "message": "Money movement type is not normalized by adapter version 1.0",
                },
            ),
        )
    if amount == 0:
        errors.append(
            {"code": "alfa_money_move_zero", "message": "Money movement must not be zero"}
        )
    operation_type, subtype = classification
    if amount is not None:
        if subtype in {"deposit", "coupon", "dividend"} and amount < 0:
            errors.append(
                {
                    "code": "alfa_money_move_direction_conflict",
                    "message": "Money movement sign conflicts with its reported type",
                }
            )
        if subtype == "withdrawal" and amount > 0:
            errors.append(
                {
                    "code": "alfa_money_move_direction_conflict",
                    "message": "Money movement sign conflicts with its reported type",
                }
            )
    if (
        errors
        or occurred_at is None
        or time_precision is None
        or amount is None
        or amount == 0
        or currency is None
    ):
        return ParsedRow(
            sequence_number=sequence_number,
            source_sheet="money_moves",
            source_row_number=source_index,
            raw_data=_raw_data(money_move),
            errors=tuple(errors),
        )

    payload: dict[str, object]
    if operation_type == "cash_movement":
        payload = {
            "direction": subtype,
            "amount": decimal_to_json(abs(amount)),
            "currency": currency,
        }
    elif operation_type == "income":
        payload = {
            "income_type": subtype,
            "amount": decimal_to_json(abs(amount)),
            "currency": currency,
        }
    else:
        payload = {"amount": decimal_to_json(abs(amount)), "currency": currency}
    return ParsedRow(
        sequence_number=sequence_number,
        source_sheet="money_moves",
        source_row_number=source_index,
        raw_data=_raw_data(money_move),
        normalized_candidate={
            "occurred_at": occurred_at,
            "time_precision": time_precision,
            "operation_type": operation_type,
            "payload": payload,
        },
    )


def _classify_money_move(group: str, kind: str) -> tuple[str, str] | None:
    combined = f"{group} {kind}"
    if "тариф" in combined:
        return "fee", "fee"
    if "купон" in group:
        return "income", "coupon"
    if "дивиденд" in group:
        return "income", "dividend"
    if "внесено" in group or "зачислено по распоряжению клиента" in group:
        return "cash_movement", "deposit"
    if "выведено" in group or "списано по распоряжению клиента" in group:
        return "cash_movement", "withdrawal"
    return None


def _datetime(
    value: str | None,
    field: str,
    errors: list[Diagnostic],
) -> tuple[str | None, str | None]:
    raw = _text(value)
    parsed: datetime | None = None
    try:
        candidate = datetime.fromisoformat(raw)
        if candidate.tzinfo is None and candidate.utcoffset() is None:
            parsed = candidate
    except ValueError:
        pass
    if parsed is None:
        for pattern in ("%d.%m.%Y %H:%M:%S", "%d.%m.%Y %H:%M", "%d.%m.%Y"):
            try:
                parsed = datetime.strptime(raw, pattern)
                break
            except ValueError:
                continue
    if parsed is None:
        errors.append(
            {
                "code": "alfa_datetime_invalid",
                "message": f"Reported {field} date and time is invalid",
            }
        )
        return None, None
    precision = "date" if len(raw) == 10 else "second"
    if parsed.microsecond:
        precision = "millisecond" if parsed.microsecond % 1000 == 0 else "microsecond"
    return parsed.replace(tzinfo=_TIMEZONE).isoformat(), precision


def _decimal(
    value: str | None,
    adapter: TypeAdapter[Decimal],
    field: str,
    errors: list[Diagnostic],
) -> Decimal | None:
    raw = _text(value).replace("\u00a0", "").replace(" ", "").replace(",", ".")
    if not raw:
        errors.append(
            {"code": "alfa_decimal_missing", "message": f"Reported {field} is required"}
        )
        return None
    try:
        parsed = Decimal(raw)
        return adapter.validate_python(decimal_to_json(parsed))
    except (InvalidOperation, ValidationError):
        errors.append(
            {"code": "alfa_decimal_invalid", "message": f"Reported {field} is invalid"}
        )
        return None


def _optional_decimal(
    value: str | None,
    adapter: TypeAdapter[Decimal],
    field: str,
    errors: list[Diagnostic],
) -> Decimal | None:
    if not _text(value):
        return None
    return _decimal(value, adapter, field, errors)


def _currency(value: str | None, errors: list[Diagnostic]) -> str | None:
    try:
        return _CURRENCY_ADAPTER.validate_python(_text(value).upper())
    except ValidationError:
        errors.append(
            {"code": "alfa_currency_invalid", "message": "Reported currency is invalid"}
        )
        return None


def _excluded_row(
    element: ElementTree.Element,
    *,
    sequence_number: int,
    source_section: str,
    code: str,
    message: str,
    source_index: int | None = None,
) -> ParsedRow:
    return ParsedRow(
        sequence_number=sequence_number,
        source_sheet=source_section,
        source_row_number=source_index,
        raw_data=_raw_data(element),
        status=ImportRowStatus.EXCLUDED,
        warnings=({"code": code, "message": message},),
    )


def _raw_data(element: ElementTree.Element) -> dict[str, object]:
    result: dict[str, object] = {}
    for child in element:
        key = _local_name(child.tag)
        value: object = _raw_data(child) if len(child) else _text(child.text)
        existing = result.get(key)
        if existing is None:
            result[key] = value
        elif isinstance(existing, list):
            existing.append(value)
        else:
            result[key] = [existing, value]
    return result


def _required_child(element: ElementTree.Element, name: str) -> ElementTree.Element:
    child = _child(element, name)
    if child is None:
        raise AssertionError(f"Required XML child is missing: {name}")
    return child


def _child(element: ElementTree.Element, name: str) -> ElementTree.Element | None:
    return next((item for item in element if _local_name(item.tag) == name), None)


def _children(
    element: ElementTree.Element, name: str
) -> tuple[ElementTree.Element, ...]:
    return tuple(item for item in element if _local_name(item.tag) == name)


def _child_text(element: ElementTree.Element, name: str) -> str | None:
    child = _child(element, name)
    return child.text if child is not None else None


def _text(value: object) -> str:
    return "" if value is None else str(value).strip()


def _local_name(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


def _layout_error(code: str, message: str) -> _XmlLayout:
    return _XmlLayout(matched=False, diagnostics=({"code": code, "message": message},))
