from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import date
from functools import cache
from typing import BinaryIO, Protocol

from imports.models import ImportCompleteness, ImportFileFormat, ImportRowStatus

_FORMAT_ID_PATTERN = re.compile(r"^[a-z][a-z0-9]*(?:[._-][a-z0-9]+)*$")
_VERSION_PATTERN = re.compile(r"^[0-9]+(?:\.[0-9]+){0,2}$")


@dataclass(frozen=True, slots=True)
class ImportDocument:
    original_filename: str
    declared_format: ImportFileFormat
    size_bytes: int
    sha256: str
    stream: BinaryIO
    document_index: int = 0
    bundle_documents: tuple[ImportDocument, ...] = ()


@dataclass(frozen=True, slots=True)
class DetectedDocument:
    document_index: int
    document_type: str


@dataclass(frozen=True, slots=True)
class DetectionResult:
    matched: bool
    completeness: ImportCompleteness = ImportCompleteness.UNKNOWN
    diagnostics: tuple[dict[str, object], ...] = ()
    reporting_period_start: date | None = None
    reporting_period_end: date | None = None
    documents: tuple[DetectedDocument, ...] = ()


@dataclass(frozen=True, slots=True)
class ParsedRow:
    sequence_number: int
    raw_data: dict[str, object]
    normalized_candidate: dict[str, object] | None = None
    reconciliation_data: dict[str, object] | None = None
    status: ImportRowStatus | None = None
    fingerprint: str | None = None
    source_page: int | None = None
    source_sheet: str | None = None
    source_row_number: int | None = None
    source_document_index: int = 0
    warnings: tuple[dict[str, object], ...] = ()
    errors: tuple[dict[str, object], ...] = ()


@dataclass(frozen=True, slots=True)
class ParsedImport:
    rows: tuple[ParsedRow, ...]
    diagnostics: tuple[dict[str, object], ...] = ()


@dataclass(frozen=True, slots=True)
class ValidationResult:
    rows: tuple[ParsedRow, ...]
    diagnostics: tuple[dict[str, object], ...] = ()


class ImportAdapter(Protocol):
    format_id: str
    version: str
    supported_file_formats: frozenset[ImportFileFormat]

    def detect(self, document: ImportDocument) -> DetectionResult: ...

    def parse(self, document: ImportDocument) -> ParsedImport: ...

    def validate(self, parsed: ParsedImport) -> ValidationResult: ...


@dataclass(frozen=True, slots=True)
class AdapterDescriptor:
    format_id: str
    version: str
    supported_file_formats: tuple[ImportFileFormat, ...]


@dataclass(slots=True)
class AdapterRegistry:
    _adapters: dict[tuple[str, str], ImportAdapter] = field(default_factory=dict)

    def register(self, adapter: ImportAdapter) -> None:
        if _FORMAT_ID_PATTERN.fullmatch(adapter.format_id) is None:
            raise ValueError("Invalid adapter format_id")
        if _VERSION_PATTERN.fullmatch(adapter.version) is None:
            raise ValueError("Invalid adapter version")
        if not adapter.supported_file_formats:
            raise ValueError("Adapter must support at least one file format")
        key = (adapter.format_id, adapter.version)
        if key in self._adapters:
            raise ValueError("Adapter version is already registered")
        self._adapters[key] = adapter

    def get(self, format_id: str, version: str) -> ImportAdapter | None:
        return self._adapters.get((format_id, version))

    def candidates(self, file_format: ImportFileFormat) -> tuple[ImportAdapter, ...]:
        return tuple(
            adapter
            for _, adapter in sorted(self._adapters.items())
            if file_format in adapter.supported_file_formats
        )

    def descriptors(self) -> tuple[AdapterDescriptor, ...]:
        return tuple(
            AdapterDescriptor(
                format_id=adapter.format_id,
                version=adapter.version,
                supported_file_formats=tuple(sorted(adapter.supported_file_formats)),
            )
            for _, adapter in sorted(self._adapters.items())
        )


@cache
def get_adapter_registry() -> AdapterRegistry:
    from imports.alfa_broker_xml import AlfaBrokerXmlAdapter
    from imports.bybit_spot_csv import BybitSpotCsvBundleAdapter
    from imports.tbank_broker_xlsx import TbankBrokerXlsxAdapter
    from imports.universal_broker_csv import UniversalBrokerCsvAdapter

    registry = AdapterRegistry()
    registry.register(AlfaBrokerXmlAdapter())
    registry.register(BybitSpotCsvBundleAdapter())
    registry.register(TbankBrokerXlsxAdapter())
    registry.register(UniversalBrokerCsvAdapter())
    return registry
