from uuid import uuid4

from imports.instrument_matching import _reference_has_match


def test_trusted_reference_is_known_when_any_identifier_matches() -> None:
    instrument_id = uuid4()
    reference: dict[str, object] = {
        "isin": "RU000A10TEST",
        "provider": "tbank",
        "provider_code": "SYNBOND",
    }

    assert _reference_has_match(
        reference,
        isin_matches={},
        provider_matches={("tbank", "SYNBOND"): instrument_id},
        crypto_matches={},
    )


def test_trusted_reference_is_unknown_when_no_identifier_matches() -> None:
    reference: dict[str, object] = {
        "crypto_asset_code": "SYNTH",
        "auto_create": True,
    }

    assert not _reference_has_match(
        reference,
        isin_matches={},
        provider_matches={},
        crypto_matches={},
    )
