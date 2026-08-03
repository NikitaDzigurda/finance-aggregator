from hashlib import sha256
from io import BytesIO
from pathlib import Path

import pytest

from imports.adapters import (
    AdapterRegistry,
    DetectionResult,
    ImportDocument,
    ParsedImport,
    ValidationResult,
)
from imports.models import ImportFileFormat
from imports.storage import (
    FileTooLargeError,
    InvalidStorageKeyError,
    LocalObjectStorage,
)


class _FakeAdapter:
    format_id = "universal_broker"
    version = "1.0"
    supported_file_formats = frozenset({ImportFileFormat.CSV})

    def detect(self, document: ImportDocument) -> DetectionResult:
        return DetectionResult(matched=document.declared_format == ImportFileFormat.CSV)

    def parse(self, document: ImportDocument) -> ParsedImport:
        del document
        return ParsedImport(rows=())

    def validate(self, parsed: ParsedImport) -> ValidationResult:
        return ValidationResult(rows=parsed.rows)


def test_local_storage_hashes_limits_and_confines_paths(tmp_path: Path) -> None:
    root = tmp_path / "storage"
    storage = LocalObjectStorage(root, max_file_size_bytes=64)
    content = b"Date,Type\n2026-08-03,Buy\n"
    stored = storage.save(BytesIO(content), suffix=".csv")

    assert stored.size_bytes == len(content)
    assert stored.sha256 == sha256(content).hexdigest()
    assert stored.key.endswith(".csv")
    assert storage.exists(stored.key)
    with storage.open(stored.key) as source:
        assert source.read() == content

    with pytest.raises(FileTooLargeError):
        storage.save(BytesIO(b"x" * 65), suffix=".csv")
    with pytest.raises(InvalidStorageKeyError):
        storage.open("../outside.csv")


def test_adapter_registry_keeps_versions_explicit() -> None:
    registry = AdapterRegistry()
    adapter = _FakeAdapter()
    registry.register(adapter)

    assert registry.get("universal_broker", "1.0") is adapter
    assert registry.candidates(ImportFileFormat.CSV) == (adapter,)
    assert registry.descriptors()[0].version == "1.0"
    with pytest.raises(ValueError, match="already registered"):
        registry.register(adapter)
