from hashlib import sha256
from io import BytesIO
from pathlib import Path

from imports.adapters import ImportDocument
from imports.bybit_spot_csv import BybitSpotCsvBundleAdapter
from imports.models import ImportFileFormat, ImportRowStatus

_FIXTURES = Path(__file__).parents[1] / "docs" / "fixtures"
_NAMES = (
    "bybit_spot_trade_history_synthetic_v1.csv",
    "bybit_uta_asset_change_details_synthetic_v1.csv",
    "bybit_funding_asset_change_details_synthetic_v1.csv",
    "bybit_withdraw_deposit_history_synthetic_v1.csv",
)


def _bundle(
    *, replacements: dict[str, tuple[bytes, bytes]] | None = None, missing: str | None = None
) -> ImportDocument:
    documents: list[ImportDocument] = []
    for name in _NAMES:
        if name == missing:
            continue
        payload = (_FIXTURES / name).read_bytes()
        if replacements and name in replacements:
            old, new = replacements[name]
            payload = payload.replace(old, new)
        stream = BytesIO(payload)
        documents.append(
            ImportDocument(
                original_filename=name,
                declared_format=ImportFileFormat.CSV,
                size_bytes=len(payload),
                sha256=sha256(payload).hexdigest(),
                stream=stream,
                document_index=len(documents),
            )
        )
    first = documents[0]
    return ImportDocument(
        original_filename=first.original_filename,
        declared_format=first.declared_format,
        size_bytes=first.size_bytes,
        sha256=first.sha256,
        stream=first.stream,
        bundle_documents=tuple(documents),
    )


def test_bybit_bundle_produces_exact_two_leg_trades_fees_and_transfer() -> None:
    adapter = BybitSpotCsvBundleAdapter()
    document = _bundle()

    detection = adapter.detect(document)
    parsed = adapter.parse(document)

    assert detection.matched is True
    assert detection.diagnostics == ()
    assert detection.reporting_period_start.isoformat() == "2026-01-15"
    assert detection.reporting_period_end.isoformat() == "2026-03-21"
    assert {item.document_type for item in detection.documents} == {
        "spot_trade_history",
        "uta_asset_change_details",
        "funding_asset_change_details",
        "withdraw_deposit_history",
    }
    candidates = [row.normalized_candidate for row in parsed.rows if row.normalized_candidate]
    assert [item["operation_type"] for item in candidates] == [
        "crypto_trade",
        "fee",
        "crypto_trade",
        "fee",
        "crypto_transfer",
        "fee",
        "balance_adjustment",
        "balance_adjustment",
        "balance_adjustment",
        "balance_adjustment",
    ]
    assert candidates[0]["payload"] == {
        "sold_instrument_reference": {
            "crypto_asset_code": "USDT",
            "auto_create": True,
            "name": "USDT",
            "instrument_type": "crypto_asset",
            "currency": "USD",
        },
        "sold_quantity": "100",
        "bought_instrument_reference": {
            "crypto_asset_code": "SYNTH",
            "auto_create": True,
            "name": "SYNTH",
            "instrument_type": "crypto_asset",
            "currency": "USD",
        },
        "bought_quantity": "5",
    }
    assert candidates[1]["payload"] == {
        "instrument_reference": {
            "crypto_asset_code": "SYNTH",
            "auto_create": True,
            "name": "SYNTH",
            "instrument_type": "crypto_asset",
            "currency": "USD",
        },
        "quantity": "0.005",
    }
    assert candidates[3]["payload"]["instrument_reference"]["crypto_asset_code"] == "USDT"
    assert candidates[4]["payload"]["quantity"] == "10"
    assert candidates[5]["payload"]["quantity"] == "0.1"
    assert sum(row.status is ImportRowStatus.EXCLUDED for row in parsed.rows) == 4
    assert sum(
        candidate["operation_type"] == "balance_adjustment" for candidate in candidates
    ) == 4
    assert all("Uid" not in row.raw_data for row in parsed.rows)


def test_bybit_bundle_missing_document_is_blocked_with_safe_diagnostic() -> None:
    document = _bundle(missing="bybit_uta_asset_change_details_synthetic_v1.csv")

    detection = BybitSpotCsvBundleAdapter().detect(document)

    diagnostic = next(
        item
        for item in detection.diagnostics
        if item["code"] == "bybit_bundle_documents_invalid"
    )
    assert diagnostic["missing_document_types"] == ["uta_asset_change_details"]
    assert "900000001" not in str(detection.diagnostics)


def test_bybit_bundle_uid_mismatch_and_missing_trade_leg_are_safe_errors() -> None:
    uid_mismatch = _bundle(
        replacements={
            "bybit_withdraw_deposit_history_synthetic_v1.csv": (
                b"900000001",
                b"900000002",
            )
        }
    )
    mismatch_detection = BybitSpotCsvBundleAdapter().detect(uid_mismatch)
    assert {item["code"] for item in mismatch_detection.diagnostics} >= {
        "bybit_bundle_uid_mismatch"
    }
    assert "900000001" not in str(mismatch_detection.diagnostics)
    assert "900000002" not in str(mismatch_detection.diagnostics)

    missing_leg = _bundle(
        replacements={
            "bybit_uta_asset_change_details_synthetic_v1.csv": (
                (
                    b"900000001,USDT,SYNTHUSDT,TRADE,BUY,-100.00000000000000000000,"
                    b"0.00000000000000000000,20.00000000000000000000,"
                    b"0.00000000000000000000,0.00000000000000000000,"
                    b"-100.00000000000000000000,-100.00000000000000000000,"
                    b"100.00000000000000000000,--,2026-01-15 10:00:00\n"
                ),
                b"",
            )
        }
    )
    parsed = BybitSpotCsvBundleAdapter().parse(missing_leg)
    assert "bybit_trade_legs_invalid" in {
        item["code"] for row in parsed.rows for item in row.errors
    }


def test_bybit_unpaired_internal_transfer_requires_review() -> None:
    document = _bundle(
        replacements={
            "bybit_funding_asset_change_details_synthetic_v1.csv": (
                b",USDT,50.000000000000000000,Transfer in,",
                b",USDT,51.000000000000000000,Transfer in,",
            )
        }
    )

    parsed = BybitSpotCsvBundleAdapter().parse(document)

    assert sum(
        any(item["code"] == "bybit_internal_transfer_match_invalid" for item in row.warnings)
        for row in parsed.rows
    ) == 2
