from __future__ import annotations

import re
from dataclasses import dataclass
from typing import BinaryIO
from xml.parsers import expat

_XML_NAME_START = re.compile(rb"[A-Za-z_:]")
_HTML_ROOTS = frozenset({"html", "xhtml"})


@dataclass(frozen=True, slots=True)
class XmlSecurityLimits:
    max_size_bytes: int
    max_depth: int
    max_elements: int
    max_value_length: int
    max_attributes_per_element: int = 128


@dataclass(frozen=True, slots=True)
class XmlDocumentMetadata:
    root_element: str
    size_bytes: int
    element_count: int
    maximum_depth: int


class XmlSecurityError(ValueError):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


def validate_xml_document(
    source: BinaryIO,
    *,
    limits: XmlSecurityLimits,
) -> XmlDocumentMetadata:
    """Validate XML in one bounded pass without resolving any external resource."""
    source.seek(0)
    prefix = source.read(4096)
    source.seek(0)
    if not _has_xml_signature(prefix):
        raise XmlSecurityError(
            "import_file_signature_invalid",
            "File content does not match the declared format",
        )

    depth = 0
    maximum_depth = 0
    element_count = 0
    text_lengths: list[int] = []
    root_element: str | None = None

    parser = expat.ParserCreate(namespace_separator="}")
    parser.buffer_text = False
    parser.SetParamEntityParsing(expat.XML_PARAM_ENTITY_PARSING_NEVER)

    def start_element(name: str, attributes: dict[str, str]) -> None:
        nonlocal depth, element_count, maximum_depth, root_element
        depth += 1
        element_count += 1
        maximum_depth = max(maximum_depth, depth)
        if root_element is None:
            root_element = _local_name(name)
            if root_element.casefold() in _HTML_ROOTS:
                raise XmlSecurityError(
                    "import_file_signature_invalid",
                    "File content does not match the declared format",
                )
        if depth > limits.max_depth:
            raise XmlSecurityError(
                "import_xml_depth_limit_exceeded",
                "XML document exceeds the configured nesting depth limit",
            )
        if element_count > limits.max_elements:
            raise XmlSecurityError(
                "import_xml_element_limit_exceeded",
                "XML document exceeds the configured element count limit",
            )
        if len(attributes) > limits.max_attributes_per_element:
            raise XmlSecurityError(
                "import_xml_attribute_limit_exceeded",
                "XML element exceeds the configured attribute count limit",
            )
        if any(len(value) > limits.max_value_length for value in attributes.values()):
            raise XmlSecurityError(
                "import_xml_value_limit_exceeded",
                "XML document contains a value longer than the configured limit",
            )
        text_lengths.append(0)

    def end_element(_name: str) -> None:
        nonlocal depth
        text_lengths.pop()
        depth -= 1

    def character_data(value: str) -> None:
        if not text_lengths:
            return
        text_lengths[-1] += len(value)
        if text_lengths[-1] > limits.max_value_length:
            raise XmlSecurityError(
                "import_xml_value_limit_exceeded",
                "XML document contains a value longer than the configured limit",
            )

    def processing_instruction(target: str, value: str) -> None:
        if len(target) > limits.max_value_length or len(value) > limits.max_value_length:
            raise XmlSecurityError(
                "import_xml_value_limit_exceeded",
                "XML document contains a value longer than the configured limit",
            )

    def comment(value: str) -> None:
        if len(value) > limits.max_value_length:
            raise XmlSecurityError(
                "import_xml_value_limit_exceeded",
                "XML document contains a value longer than the configured limit",
            )

    def forbid_dtd(*_arguments: object) -> None:
        raise XmlSecurityError(
            "import_xml_dtd_forbidden",
            "DTD declarations are not allowed in uploaded XML",
        )

    def forbid_entity(*_arguments: object) -> None:
        raise XmlSecurityError(
            "import_xml_entity_forbidden",
            "Entity declarations are not allowed in uploaded XML",
        )

    def forbid_external_entity(*_arguments: object) -> int:
        raise XmlSecurityError(
            "import_xml_external_entity_forbidden",
            "External entities are not allowed in uploaded XML",
        )

    parser.StartElementHandler = start_element
    parser.EndElementHandler = end_element
    parser.CharacterDataHandler = character_data
    parser.ProcessingInstructionHandler = processing_instruction
    parser.CommentHandler = comment
    parser.StartDoctypeDeclHandler = forbid_dtd
    parser.EntityDeclHandler = forbid_entity
    parser.UnparsedEntityDeclHandler = forbid_entity
    parser.NotationDeclHandler = forbid_entity
    parser.ExternalEntityRefHandler = forbid_external_entity
    parser.SkippedEntityHandler = forbid_entity

    size_bytes = 0
    try:
        while chunk := source.read(64 * 1024):
            size_bytes += len(chunk)
            if size_bytes > limits.max_size_bytes:
                raise XmlSecurityError(
                    "import_file_too_large",
                    "Uploaded file exceeds the configured size limit",
                )
            parser.Parse(chunk, False)
        parser.Parse(b"", True)
    except XmlSecurityError:
        raise
    except expat.ExpatError as exc:
        raise XmlSecurityError(
            "import_xml_malformed",
            "Uploaded XML is malformed",
        ) from exc
    finally:
        source.seek(0)

    if root_element is None:
        raise XmlSecurityError(
            "import_xml_malformed",
            "Uploaded XML is malformed",
        )
    return XmlDocumentMetadata(
        root_element=root_element,
        size_bytes=size_bytes,
        element_count=element_count,
        maximum_depth=maximum_depth,
    )


def _has_xml_signature(prefix: bytes) -> bool:
    if not prefix:
        return False
    decoded_prefix: str | None = None
    if prefix.startswith((b"\xff\xfe\x00\x00", b"\x00\x00\xfe\xff")):
        decoded_prefix = prefix.decode("utf-32", errors="ignore")
    elif prefix.startswith((b"\xff\xfe", b"\xfe\xff")):
        decoded_prefix = prefix.decode("utf-16", errors="ignore")
    if decoded_prefix is not None:
        candidate = decoded_prefix.lstrip("\ufeff \t\r\n")
        return candidate.startswith("<?xml") or (
            candidate.startswith("<")
            and len(candidate) > 1
            and (candidate[1].isalpha() or candidate[1] in {"_", ":"})
        )

    candidate_bytes = prefix
    if candidate_bytes.startswith(b"\xef\xbb\xbf"):
        candidate_bytes = candidate_bytes[3:]
    candidate_bytes = candidate_bytes.lstrip(b" \t\r\n")
    if candidate_bytes.startswith(b"<?xml"):
        return True
    return (
        candidate_bytes.startswith(b"<")
        and len(candidate_bytes) > 1
        and _XML_NAME_START.fullmatch(candidate_bytes[1:2]) is not None
    )


def _local_name(expanded_name: str) -> str:
    return expanded_name.rsplit("}", 1)[-1]
