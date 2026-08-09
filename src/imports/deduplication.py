from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from dataclasses import replace
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from imports.adapters import ParsedRow
from imports.models import ImportRowStatus
from operations.models import OperationModel


def import_fingerprint(
    *,
    account_id: UUID,
    source_provider: str,
    candidate: Mapping[str, object],
) -> str:
    operation_type = candidate.get("operation_type")
    source_operation_id = candidate.get("source_operation_id")
    if isinstance(source_operation_id, str) and source_operation_id:
        material: dict[str, object] = {
            "version": 1,
            "scope": "external_id",
            "account_id": str(account_id),
            "source_provider": source_provider,
            "source_operation_id": source_operation_id,
            "operation_type": operation_type,
        }
    else:
        material = {
            "version": 1,
            "scope": "economic",
            "account_id": str(account_id),
            "source_provider": source_provider,
            "operation_type": operation_type,
            "occurred_at": candidate.get("occurred_at"),
            "time_precision": candidate.get("time_precision"),
            "payload": candidate.get("payload"),
            "note": candidate.get("note"),
        }
    encoded = json.dumps(
        material,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode()
    return hashlib.sha256(encoded).hexdigest()


def duplicate_override_fingerprint(
    base_fingerprint: str,
    *,
    batch_id: UUID,
    row_id: UUID,
) -> str:
    material = f"override:v1:{base_fingerprint}:{batch_id}:{row_id}".encode()
    return hashlib.sha256(material).hexdigest()


async def apply_import_deduplication(
    session: AsyncSession,
    rows: tuple[ParsedRow, ...],
    *,
    account_id: UUID,
    source_provider: str,
) -> tuple[ParsedRow, ...]:
    fingerprinted: list[ParsedRow] = []
    fingerprints: set[str] = set()
    for row in rows:
        if row.normalized_candidate is None:
            fingerprinted.append(row)
            continue
        fingerprint = import_fingerprint(
            account_id=account_id,
            source_provider=source_provider,
            candidate=row.normalized_candidate,
        )
        fingerprints.add(fingerprint)
        fingerprinted.append(replace(row, fingerprint=fingerprint))

    existing = set(
        await session.scalars(
            select(OperationModel.fingerprint).where(
                OperationModel.account_id == account_id,
                OperationModel.fingerprint.in_(fingerprints),
            )
        )
    )
    seen: set[str] = set()
    result: list[ParsedRow] = []
    for row in fingerprinted:
        row_fingerprint = row.fingerprint
        if row_fingerprint is None or row.errors:
            result.append(row)
            continue
        if row_fingerprint not in existing and row_fingerprint not in seen:
            seen.add(row_fingerprint)
            result.append(row)
            continue
        duplicate_warning: dict[str, object] = {
            "code": "import_duplicate",
            "message": "A matching imported operation already exists",
        }
        warnings = (*row.warnings, duplicate_warning)
        result.append(
            replace(
                row,
                status=ImportRowStatus.DUPLICATE,
                warnings=warnings,
            )
        )
    return tuple(result)
