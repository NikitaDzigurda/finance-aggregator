from hashlib import sha256
from io import BytesIO
from pathlib import Path
from xml.etree import ElementTree

import pytest

from imports.adapters import AdapterRegistry, ImportDocument
from imports.alfa_broker_xml import AlfaBrokerXmlAdapter
from imports.models import ImportCompleteness, ImportFileFormat, ImportRowStatus
from imports.processor import ImportProcessingError, _detect_adapter

_FIXTURE = (
    Path(__file__).parents[1]
    / "docs"
    / "fixtures"
    / "alfa_broker_report_import_synthetic_v1.xml"
)


def _document(payload: bytes) -> ImportDocument:
    return ImportDocument(
        original_filename="synthetic-alfa.xml",
        declared_format=ImportFileFormat.XML,
        size_bytes=len(payload),
        sha256=sha256(payload).hexdigest(),
        stream=BytesIO(payload),
    )


def test_alfa_fixture_detects_and_separates_ledger_from_reconciliation() -> None:
    adapter = AlfaBrokerXmlAdapter()
    document = _document(_FIXTURE.read_bytes())

    detection = adapter.detect(document)
    parsed = adapter.parse(document)

    assert detection.matched is True
    assert detection.completeness is ImportCompleteness.PERIOD_LEDGER
    assert detection.reporting_period_start is not None
    assert detection.reporting_period_end is not None
    assert detection.reporting_period_start.isoformat() == "2026-01-01"
    assert detection.reporting_period_end.isoformat() == "2026-01-31"
    assert {item["code"] for item in detection.diagnostics} == {
        "alfa_positions_reconciliation_available",
        "alfa_money_totals_reconciliation_available",
    }
    assert len(parsed.rows) == 11

    trade, fee = parsed.rows[:2]
    assert trade.normalized_candidate == {
        "occurred_at": "2026-01-10T10:15:30+03:00",
        "time_precision": "second",
        "source_operation_id": "1000001",
        "operation_type": "trade",
        "payload": {
            "side": "buy",
            "quantity": "10",
            "price": "1000.00",
            "price_currency": "RUB",
            "instrument_reference": {
                "provider": "alfa_broker_xml_import",
                "provider_code": "SYNBOND",
                "currency": "RUB",
                "auto_create": True,
                "name": "Synthetic Bond Fund",
                "instrument_type": "fund",
            },
        },
    }
    assert fee.normalized_candidate == {
        "occurred_at": "2026-01-10T10:15:30+03:00",
        "time_precision": "second",
        "source_operation_id": "1000001",
        "operation_type": "fee",
        "payload": {
            "amount": "10.00",
            "currency": "RUB",
            "instrument_reference": {
                "provider": "alfa_broker_xml_import",
                "provider_code": "SYNBOND",
                "currency": "RUB",
                "auto_create": True,
                "name": "Synthetic Bond Fund",
                "instrument_type": "fund",
            },
        },
    }
    assert trade.reconciliation_data == {
        "position_effects": [{"key": "provider_code:SYNBOND", "change": "10"}]
    }

    candidates = [row.normalized_candidate for row in parsed.rows]
    assert [candidate["operation_type"] for candidate in candidates if candidate] == [
        "trade",
        "fee",
        "cash_movement",
        "income",
        "balance_adjustment",
    ]
    assert candidates[3] == {
        "occurred_at": "2026-01-05T09:00:00+03:00",
        "time_precision": "second",
        "operation_type": "cash_movement",
        "payload": {"direction": "deposit", "amount": "10000.00", "currency": "RUB"},
    }
    assert candidates[6] == {
        "occurred_at": "2026-01-20T12:00:00+03:00",
        "time_precision": "second",
        "operation_type": "income",
        "payload": {"income_type": "coupon", "amount": "250.00", "currency": "RUB"},
    }

    unfinished = parsed.rows[2]
    assert unfinished.status is ImportRowStatus.EXCLUDED
    assert unfinished.normalized_candidate is None
    assert unfinished.warnings[0]["code"] == "alfa_unfinished_trade_information"
    linked_money = parsed.rows[4:6]
    assert all(row.status is ImportRowStatus.EXCLUDED for row in linked_money)
    assert all(row.normalized_candidate is None for row in linked_money)
    assert {row.warnings[0]["code"] for row in linked_money} == {
        "alfa_trade_money_reconciliation_only"
    }
    assert parsed.rows[7].reconciliation_data == {
        "controls": [
            {
                "kind": "position",
                "key": "provider_code:SYNBOND",
                "opening": "0",
                "closing": "10.000000",
            }
        ]
    }
    assert parsed.rows[9].reconciliation_data == {
        "controls": [
            {
                "kind": "cash",
                "key": "RUB",
                "opening": "50000.00",
                "closing": "50240.00",
            }
        ]
    }
    transfer = parsed.rows[-1]
    assert transfer.status is None
    assert transfer.normalized_candidate == {
        "occurred_at": "2026-01-15T00:00:00+03:00",
        "time_precision": "date",
        "operation_type": "balance_adjustment",
        "payload": {
            "instrument_reference": {
                "provider": "alfa_broker_xml_import",
                "provider_code": "SYNSHARE",
                "auto_create": True,
                "name": "Synthetic Share",
                "instrument_type": "stock",
                "currency": "RUB",
            },
            "quantity_change": "3",
            "reason": "Alfa-Investments reported securities transfer",
        },
    }
    assert transfer.warnings == ()


def test_alfa_trade_side_uses_quantity_sign_not_trade_sum() -> None:
    payload = _FIXTURE.read_bytes().replace(
        b"<summ_trade>10000.00</summ_trade>",
        b"<summ_trade>-10000.00</summ_trade>",
        1,
    ).replace(b"<qty>10</qty>", b"<qty>-10.125</qty>", 1)

    trade = AlfaBrokerXmlAdapter().parse(_document(payload)).rows[0]

    assert trade.normalized_candidate is not None
    trade_payload = trade.normalized_candidate["payload"]
    assert isinstance(trade_payload, dict)
    assert trade_payload["side"] == "sell"
    assert trade_payload["quantity"] == "10.125"


def test_alfa_trade_linked_money_requires_completed_trade() -> None:
    payload = _FIXTURE.read_bytes().replace(
        b"<trd_no>1000001</trd_no>", b"<trd_no>9999999</trd_no>", 1
    )

    parsed = AlfaBrokerXmlAdapter().parse(_document(payload))
    money_error = parsed.rows[4]

    assert money_error.normalized_candidate is None
    assert money_error.errors == (
        {
            "code": "alfa_trade_money_link_missing",
            "message": "Trade-linked money movement has no completed trade",
        },
    )


def test_alfa_unlinked_withdrawal_and_bank_tariff_are_normalized() -> None:
    root = ElementTree.fromstring(_FIXTURE.read_bytes())
    money_moves = root.find("money_moves")
    assert money_moves is not None
    withdrawal = money_moves.findall("money_move")[0]
    withdrawal_group = withdrawal.find("oper_group")
    withdrawal_volume = withdrawal.find("volume")
    assert withdrawal_group is not None
    assert withdrawal_volume is not None
    withdrawal_group.text = "Списано по распоряжению Клиента"
    withdrawal_volume.text = "-100.25"
    tariff = money_moves.findall("money_move")[3]
    tariff_group = tariff.find("oper_group")
    tariff_type = tariff.find("oper_type")
    tariff_volume = tariff.find("volume")
    assert tariff_group is not None
    assert tariff_type is not None
    assert tariff_volume is not None
    tariff_group.text = "Банковский тариф"
    tariff_type.text = "Тариф"
    tariff_volume.text = "-5.75"

    parsed = AlfaBrokerXmlAdapter().parse(
        _document(ElementTree.tostring(root, encoding="utf-8"))
    )

    withdrawal_candidate = parsed.rows[3].normalized_candidate
    tariff_candidate = parsed.rows[6].normalized_candidate
    assert withdrawal_candidate is not None
    assert tariff_candidate is not None
    assert withdrawal_candidate["payload"] == {
        "direction": "withdrawal",
        "amount": "100.25",
        "currency": "RUB",
    }
    assert tariff_candidate["operation_type"] == "fee"
    assert tariff_candidate["payload"] == {
        "amount": "5.75",
        "currency": "RUB",
    }


def test_alfa_detection_rejects_wrong_root_and_missing_collection_safely() -> None:
    wrong_root = _FIXTURE.read_bytes().replace(b"report_broker", b"other_report", 2)
    missing_collection = _FIXTURE.read_bytes().replace(
        b"<transfers>", b"<optional_transfers>", 1
    ).replace(b"</transfers>", b"</optional_transfers>", 1)

    root_detection = AlfaBrokerXmlAdapter().detect(_document(wrong_root))
    collection_detection = AlfaBrokerXmlAdapter().detect(_document(missing_collection))

    assert root_detection.matched is False
    assert root_detection.diagnostics == (
        {
            "code": "alfa_xml_root_invalid",
            "message": "XML does not contain the required broker report root",
        },
    )
    assert collection_detection.matched is False
    assert collection_detection.diagnostics[0]["code"] == "alfa_xml_collection_missing"
    assert "Synthetic" not in str(collection_detection.diagnostics)


def test_alfa_detection_explains_standard_xml_variant() -> None:
    standard_report = (
        b'<?xml version="1.0" encoding="utf-8"?>'
        b'<Report Name="MyBroker"><Positions /></Report>'
    )

    detection = AlfaBrokerXmlAdapter().detect(_document(standard_report))

    assert detection.matched is False
    assert detection.diagnostics == (
        {
            "code": "alfa_xml_variant_unsupported",
            "message": (
                "Alfa standard XML report is not the XML variant intended for import"
            ),
        },
    )

    registry = AdapterRegistry()
    registry.register(AlfaBrokerXmlAdapter())
    with pytest.raises(ImportProcessingError) as captured:
        _detect_adapter(
            registry,
            _document(standard_report),
            "alfa_broker_xml_import",
        )
    assert captured.value.code == "alfa_xml_variant_unsupported"
