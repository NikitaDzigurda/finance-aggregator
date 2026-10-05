from hashlib import sha256
from io import BytesIO

import pytest

from imports.adapters import ImportDocument
from imports.models import ImportFileFormat
from imports.processor import ImportProcessingError, _verify_document
from imports.xml_security import XmlSecurityError, XmlSecurityLimits, validate_xml_document


def _limits(**overrides: int) -> XmlSecurityLimits:
    values = {
        "max_size_bytes": 4096,
        "max_depth": 8,
        "max_elements": 32,
        "max_value_length": 32,
    }
    values.update(overrides)
    return XmlSecurityLimits(**values)


def test_xml_signature_accepts_utf8_bom_and_leading_whitespace() -> None:
    stream = BytesIO(b"\xef\xbb\xbf \r\n\t<report_broker><positions /></report_broker>")

    metadata = validate_xml_document(stream, limits=_limits())

    assert metadata.root_element == "report_broker"
    assert metadata.element_count == 2
    assert metadata.maximum_depth == 2
    assert stream.tell() == 0


@pytest.mark.parametrize(
    ("payload", "expected_code"),
    [
        (b"<html><body>not a broker report</body></html>", "import_file_signature_invalid"),
        (
            b'<?xml version="1.0"?><!DOCTYPE report_broker '
            b'SYSTEM "https://invalid.example/external.dtd"><report_broker />',
            "import_xml_dtd_forbidden",
        ),
        (
            b'<?xml version="1.0"?><!DOCTYPE report_broker '
            b'[<!ENTITY x "sensitive-value">]><report_broker>&x;</report_broker>',
            "import_xml_dtd_forbidden",
        ),
    ],
)
def test_xml_rejects_html_dtd_and_entity_payloads(
    payload: bytes,
    expected_code: str,
) -> None:
    with pytest.raises(XmlSecurityError) as error:
        validate_xml_document(BytesIO(payload), limits=_limits())

    assert error.value.code == expected_code
    assert "sensitive-value" not in error.value.message
    assert "invalid.example" not in error.value.message


@pytest.mark.parametrize(
    ("payload", "limits", "expected_code"),
    [
        (
            b"<report_broker><a><b><c /></b></a></report_broker>",
            _limits(max_depth=3),
            "import_xml_depth_limit_exceeded",
        ),
        (
            b"<report_broker><a /><b /><c /></report_broker>",
            _limits(max_elements=3),
            "import_xml_element_limit_exceeded",
        ),
        (
            b"<report_broker><value>123456789</value></report_broker>",
            _limits(max_value_length=8),
            "import_xml_value_limit_exceeded",
        ),
    ],
)
def test_xml_resource_limits_are_enforced(
    payload: bytes,
    limits: XmlSecurityLimits,
    expected_code: str,
) -> None:
    with pytest.raises(XmlSecurityError) as error:
        validate_xml_document(BytesIO(payload), limits=limits)

    assert error.value.code == expected_code


def test_malformed_xml_returns_safe_diagnostic() -> None:
    payload = b"<report_broker><private-value>SECRET</report_broker>"

    with pytest.raises(XmlSecurityError) as error:
        validate_xml_document(BytesIO(payload), limits=_limits())

    assert error.value.code == "import_xml_malformed"
    assert "SECRET" not in error.value.message


def test_worker_revalidates_stored_xml_after_integrity_check() -> None:
    payload = (
        b'<?xml version="1.0"?><!DOCTYPE report_broker '
        b'SYSTEM "https://invalid.example/external.dtd"><report_broker />'
    )
    document = ImportDocument(
        original_filename="synthetic.xml",
        declared_format=ImportFileFormat.XML,
        size_bytes=len(payload),
        sha256=sha256(payload).hexdigest(),
        stream=BytesIO(payload),
    )

    with pytest.raises(ImportProcessingError) as error:
        _verify_document(document)

    assert error.value.code == "import_xml_dtd_forbidden"
    assert "invalid.example" not in error.value.message
