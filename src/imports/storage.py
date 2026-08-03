from __future__ import annotations

import hashlib
import os
import re
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import BinaryIO, Protocol
from uuid import uuid4

from shared.config import get_settings

_STORAGE_KEY_PATTERN = re.compile(r"^[0-9a-f]{2}/[0-9a-f]{32}\.(csv|xlsx|pdf)$")
_ALLOWED_SUFFIXES = frozenset({".csv", ".xlsx", ".pdf"})


class StorageError(Exception):
    pass


class FileTooLargeError(StorageError):
    pass


class EmptyFileError(StorageError):
    pass


class InvalidStorageKeyError(StorageError):
    pass


@dataclass(frozen=True, slots=True)
class StoredObject:
    key: str
    size_bytes: int
    sha256: str


class ObjectStorage(Protocol):
    def save(self, source: BinaryIO, *, suffix: str) -> StoredObject: ...

    def open(self, key: str) -> BinaryIO: ...

    def delete(self, key: str) -> None: ...

    def exists(self, key: str) -> bool: ...


class LocalObjectStorage:
    def __init__(self, root: Path, *, max_file_size_bytes: int) -> None:
        self._root = root.expanduser().resolve()
        self._max_file_size_bytes = max_file_size_bytes
        self._root.mkdir(mode=0o700, parents=True, exist_ok=True)
        os.chmod(self._root, 0o700)

    def save(self, source: BinaryIO, *, suffix: str) -> StoredObject:
        normalized_suffix = suffix.lower()
        if normalized_suffix not in _ALLOWED_SUFFIXES:
            raise StorageError("Unsupported storage suffix")

        object_id = uuid4().hex
        key = f"{object_id[:2]}/{object_id}{normalized_suffix}"
        target = self._path_for(key)
        target.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
        descriptor = os.open(target, flags, 0o600)
        digest = hashlib.sha256()
        size_bytes = 0

        try:
            source.seek(0)
            with os.fdopen(descriptor, "wb") as destination:
                while chunk := source.read(1024 * 1024):
                    size_bytes += len(chunk)
                    if size_bytes > self._max_file_size_bytes:
                        raise FileTooLargeError
                    digest.update(chunk)
                    destination.write(chunk)
                destination.flush()
                os.fsync(destination.fileno())
            if size_bytes == 0:
                raise EmptyFileError
        except BaseException:
            target.unlink(missing_ok=True)
            raise

        return StoredObject(key=key, size_bytes=size_bytes, sha256=digest.hexdigest())

    def open(self, key: str) -> BinaryIO:
        target = self._path_for(key)
        flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
        descriptor = os.open(target, flags)
        return os.fdopen(descriptor, "rb")

    def delete(self, key: str) -> None:
        target = self._path_for(key)
        target.unlink(missing_ok=True)

    def exists(self, key: str) -> bool:
        target = self._path_for(key)
        return target.is_file() and not target.is_symlink()

    def _path_for(self, key: str) -> Path:
        if _STORAGE_KEY_PATTERN.fullmatch(key) is None:
            raise InvalidStorageKeyError
        target = (self._root / key).resolve(strict=False)
        if not target.is_relative_to(self._root):
            raise InvalidStorageKeyError
        return target


@lru_cache
def get_object_storage() -> ObjectStorage:
    settings = get_settings()
    return LocalObjectStorage(
        settings.import_storage_root,
        max_file_size_bytes=settings.import_max_file_size_bytes,
    )
