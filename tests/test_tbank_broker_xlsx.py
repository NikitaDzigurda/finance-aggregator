from hashlib import sha256
from io import BytesIO
from pathlib import Path

from openpyxl import Workbook, load_workbook

from imports.adapters import ImportDocument
from imports.models import ImportCompleteness, ImportFileFormat
from imports.tbank_broker_xlsx import TbankBrokerXlsxAdapter

_FIXTURE = (
    Path(__file__).parents[1]
    / "docs"
    / "fixtures"
    / "tbank_broker_report_synthetic_v1.xlsx"
)


def _document(payload: bytes) -> ImportDocument:
    return ImportDocument(
        original_filename="synthetic-tbank.xlsx",
        declared_format=ImportFileFormat.XLSX,
        size_bytes=len(payload),
        sha256=sha256(payload).hexdigest(),
        stream=BytesIO(payload),
    )


def test_tbank_fixture_detects_period_and_parses_trade_and_fee_exactly() -> None:
    adapter = TbankBrokerXlsxAdapter()
    document = _document(_FIXTURE.read_bytes())

    detection = adapter.detect(document)
    parsed = adapter.parse(document)

    assert detection.matched is True
    assert detection.completeness is ImportCompleteness.PERIOD_LEDGER
    assert detection.reporting_period_start.isoformat() == "2026-01-01"
    assert detection.reporting_period_end.isoformat() == "2026-01-31"
    assert {item["code"] for item in detection.diagnostics} >= {
        "tbank_cash_reconciliation_available",
        "tbank_securities_reconciliation_available",
        "tbank_unfinished_trades_empty",
    }
    assert len(parsed.rows) == 4
    trade, fee, cash_reconciliation, security_reconciliation = parsed.rows
    assert trade.source_sheet == "broker_rep"
    assert trade.source_row_number == 10
    assert trade.normalized_candidate == {
        "occurred_at": "2026-01-10T10:15:30+03:00",
        "time_precision": "second",
        "source_operation_id": "SYN-TRADE-0001",
        "operation_type": "trade",
        "payload": {
            "side": "buy",
            "quantity": "10",
            "price": "1000",
            "price_currency": "RUB",
            "instrument_reference": {
                "provider": "tbank_broker_xlsx",
                "provider_code": "SYNBOND",
                "currency": "RUB",
            },
        },
    }
    assert trade.warnings == ()
    assert trade.reconciliation_data == {
        "position_effects": [{"key": "provider_code:SYNBOND", "change": "10"}]
    }
    assert fee.normalized_candidate == {
        "occurred_at": "2026-01-10T10:15:30+03:00",
        "time_precision": "second",
        "source_operation_id": "SYN-TRADE-0001:fee:RUB",
        "operation_type": "fee",
        "payload": {
            "amount": "10",
            "currency": "RUB",
            "instrument_reference": {
                "provider": "tbank_broker_xlsx",
                "provider_code": "SYNBOND",
                "currency": "RUB",
            },
        },
    }
    assert cash_reconciliation.status.value == "excluded"
    assert cash_reconciliation.normalized_candidate is None
    assert cash_reconciliation.warnings[0]["code"] == "tbank_cash_reconciliation_only"
    assert cash_reconciliation.reconciliation_data == {
        "controls": [
            {"kind": "cash", "key": "RUB", "opening": "50000", "closing": "50240"}
        ]
    }
    assert security_reconciliation.status.value == "excluded"
    assert security_reconciliation.normalized_candidate is None
    assert security_reconciliation.warnings[0]["code"] == (
        "tbank_security_reconciliation_only"
    )
    assert security_reconciliation.reconciliation_data == {
        "controls": [
            {
                "kind": "position",
                "key": "provider_code:SYNBOND",
                "opening": "0",
                "closing": "10",
            }
        ]
    }


def test_tbank_side_uses_report_trade_kind() -> None:
    workbook = load_workbook(_FIXTURE)
    workbook["broker_rep"]["P10"] = "Продажа"
    output = BytesIO()
    workbook.save(output)
    workbook.close()

    parsed = TbankBrokerXlsxAdapter().parse(_document(output.getvalue()))

    assert parsed.rows[0].normalized_candidate is not None
    assert parsed.rows[0].normalized_candidate["payload"]["side"] == "sell"


def test_tbank_detection_rejects_arbitrary_xlsx() -> None:
    workbook = Workbook()
    workbook.active["A1"] = "Synthetic unrelated workbook"
    output = BytesIO()
    workbook.save(output)
    workbook.close()

    detection = TbankBrokerXlsxAdapter().detect(_document(output.getvalue()))

    assert detection.matched is False
    assert detection.diagnostics[0]["code"] == "tbank_xlsx_sheet_missing"


def test_tbank_reports_unsupported_sections_without_exposing_contents() -> None:
    workbook = load_workbook(_FIXTURE)
    workbook["broker_rep"]["A43"] = "9.9 Synthetic unsupported private section"
    output = BytesIO()
    workbook.save(output)
    workbook.close()

    detection = TbankBrokerXlsxAdapter().detect(_document(output.getvalue()))

    diagnostic = next(
        item for item in detection.diagnostics if item["code"] == "tbank_section_unsupported"
    )
    assert diagnostic["count"] == 1
    assert "private" not in str(diagnostic).casefold()
