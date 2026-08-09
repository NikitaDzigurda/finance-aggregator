from hashlib import sha256
from io import BytesIO

from imports.adapters import ImportDocument
from imports.models import ImportCompleteness, ImportFileFormat
from imports.universal_broker_csv import UniversalBrokerCsvAdapter


def _document(content: bytes) -> ImportDocument:
    return ImportDocument(
        original_filename="synthetic.csv",
        declared_format=ImportFileFormat.CSV,
        size_bytes=len(content),
        sha256=sha256(content).hexdigest(),
        stream=BytesIO(content),
    )


def test_universal_broker_csv_normalizes_supported_operations_and_exact_decimals() -> None:
    content = (
        "\n".join(
            [
                "Date;Type;Symbol;ISIN;Quantity;Price;Amount;Currency;Fee;FeeCurrency;"
                "Tax;AccruedInterest;Exchange;ExternalId;Note",
                "03.08.2026;Buy;SYN;US0000000001;10,5;125,50;;USD;1,25;USD;"
                "2,50;0;XNAS;SYN-1;Synthetic buy",
                "2026-08-04;Sell;SYN;US0000000001;2;130.75;;USD;0;USD;0;0;"
                "XNAS;SYN-2;Synthetic sell",
                "2026-08-05;Dividend;SYN;US0000000001;;;1 250,75;USD;0;USD;0;0;"
                "XNAS;SYN-3;Synthetic dividend",
                "2026-08-06;Coupon;BND;US0000000002;;;12.125;EUR;0;EUR;0;0;"
                "XPAR;SYN-4;Synthetic coupon",
                "2026-08-07;Fee;;;;;3,25;USD;;;0;;;SYN-5;Synthetic fee",
                "2026-08-08;Tax;;;;;4,50;USD;;;0;;;SYN-6;Synthetic tax",
                "2026-08-09;CashIn;;;;;2000;USD;;;0;;;SYN-7;Synthetic cash in",
                "2026-08-10;CashOut;;;;;250,25;USD;;;0;;;SYN-8;Synthetic cash out",
            ]
        )
        + "\n"
    ).encode("utf-8-sig")
    adapter = UniversalBrokerCsvAdapter()
    document = _document(content)

    detection = adapter.detect(document)
    parsed = adapter.parse(document)
    validated = adapter.validate(parsed)

    assert detection.matched is True
    assert detection.completeness is ImportCompleteness.COMPLETE
    assert len(validated.rows) == 10
    assert [row.normalized_candidate["operation_type"] for row in validated.rows] == [
        "trade",
        "fee",
        "tax",
        "trade",
        "income",
        "income",
        "fee",
        "tax",
        "cash_movement",
        "cash_movement",
    ]
    buy_payload = validated.rows[0].normalized_candidate["payload"]
    assert buy_payload["quantity"] == "10.5"
    assert buy_payload["price"] == "125.50"
    assert validated.rows[1].normalized_candidate["payload"]["amount"] == "1.25"
    assert validated.rows[2].normalized_candidate["payload"]["amount"] == "2.50"
    assert validated.rows[4].normalized_candidate["payload"]["amount"] == "1250.75"
    assert all(not row.errors for row in validated.rows)


def test_universal_broker_csv_reports_structural_and_row_errors() -> None:
    adapter = UniversalBrokerCsvAdapter()
    missing_headers = _document(b"Date,Type\n2026-08-03,Buy\n")
    invalid_row = _document(
        b"Date,Type,Currency\n2026-08-03,Unsupported,USD\n"
    )

    structural = adapter.parse(missing_headers)
    row_error = adapter.parse(invalid_row)

    assert structural.rows[0].errors[0]["code"] == "csv_required_headers_missing"
    assert row_error.rows[0].errors[0]["code"] == "operation_type_unsupported"
    assert row_error.rows[0].raw_data == {
        "Date": "2026-08-03",
        "Type": "Unsupported",
        "Currency": "USD",
    }
