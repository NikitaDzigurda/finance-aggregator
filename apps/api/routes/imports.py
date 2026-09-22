from __future__ import annotations

from typing import Annotated, Any
from uuid import UUID

from fastapi import APIRouter, Body, Depends, File, Form, Query, Response, UploadFile, status
from sqlalchemy.ext.asyncio import AsyncSession

from apps.api.routes.common import not_found
from imports.adapters import AdapterRegistry, get_adapter_registry
from imports.models import ImportFileFormat, ImportStatus
from imports.schemas import (
    ImportBatchListResponse,
    ImportBatchResponse,
    ImportConfirmResponse,
    ImportFormatListResponse,
    ImportFormatResponse,
    ImportJobResponse,
    ImportPreviewResponse,
    ImportRollbackResponse,
    ImportRowListResponse,
    ImportRowResolutionRequest,
    ImportRowResponse,
    ImportStatusResponse,
    ImportUploadResponse,
    SourceProvider,
)
from imports.service import (
    ImportUploadPart,
    create_import_batch_documents,
    get_import_batch,
    get_import_preview_summary,
    list_import_batches,
    list_import_formats,
    list_import_rows,
)
from imports.storage import ObjectStorage, get_object_storage
from imports.workflow import confirm_import, resolve_import_row, rollback_import
from shared.database import get_db_session
from shared.errors import ErrorResponse

router = APIRouter(prefix="/api/v1/imports", tags=["imports"])
formats_router = APIRouter(prefix="/api/v1/import-formats", tags=["imports"])

NOT_FOUND_RESPONSE: dict[int | str, dict[str, Any]] = {
    status.HTTP_404_NOT_FOUND: {
        "model": ErrorResponse,
        "description": "Import batch or referenced account was not found",
    }
}
UPLOAD_RESPONSES: dict[int | str, dict[str, Any]] = {
    status.HTTP_200_OK: {
        "model": ImportUploadResponse,
        "description": "The same file was already uploaded to this account",
    },
    status.HTTP_413_CONTENT_TOO_LARGE: {
        "model": ErrorResponse,
        "description": "The uploaded file exceeds the configured size limit",
    },
    status.HTTP_415_UNSUPPORTED_MEDIA_TYPE: {
        "model": ErrorResponse,
        "description": "The file media type is not allowed",
    },
    **NOT_FOUND_RESPONSE,
}
WORKFLOW_RESPONSES: dict[int | str, dict[str, Any]] = {
    status.HTTP_409_CONFLICT: {
        "model": ErrorResponse,
        "description": "Import state, resolution, duplicate, or ledger dependency conflicts",
    },
    **NOT_FOUND_RESPONSE,
}
RESOLUTION_EXAMPLES = {
    "exclude": {
        "summary": "Exclude a non-operation row",
        "value": {"action": "exclude", "note": "Synthetic report total row"},
    },
    "match_instrument": {
        "summary": "Map an ambiguous row to a canonical instrument",
        "value": {
            "action": "match_instrument",
            "instrument_id": "33333333-3333-4333-8333-333333333333",
            "note": "Verified against the synthetic symbol",
        },
    },
    "allow_duplicate": {
        "summary": "Explicitly accept a reviewed economic duplicate",
        "value": {
            "action": "allow_duplicate",
            "note": "Distinct synthetic fill with the same economic fields",
        },
    },
}


@router.post(
    "",
    response_model=ImportUploadResponse,
    status_code=status.HTTP_201_CREATED,
    responses=UPLOAD_RESPONSES,
    summary="Upload an import file into traceable staging",
    description=(
        "Streams one or more CSV files, or one XLSX, XML, or PDF, into protected storage and "
        "queues parsing. XML is "
        "validated with DTD/entity prohibitions and bounded depth, element count, and value "
        "length. "
        "A repeated SHA-256 for the same account returns the existing batch with HTTP 200 and "
        "duplicate=true."
    ),
)
async def upload_import_route(
    response: Response,
    portfolio_id: Annotated[
        UUID,
        Form(
            description="Portfolio that owns the target account",
            examples=["11111111-1111-4111-8111-111111111111"],
        ),
    ],
    account_id: Annotated[
        UUID,
        Form(
            description="Account that will own confirmed operations",
            examples=["22222222-2222-4222-8222-222222222222"],
        ),
    ],
    source_provider: Annotated[
        SourceProvider,
        Form(
            description=(
                "Explicit registered adapter ID. Use tbank_broker_xlsx for the official "
                "T-Investments XLSX, alfa_broker_xml_import for the Alfa-Investments import "
                "XML, bybit_spot_csv_bundle for the four-file Bybit Spot CSV export, or "
                "universal_broker for the synthetic CSV contract. The adapter also "
                "validates the file's internal format."
            ),
            examples=[
                "tbank_broker_xlsx",
                "alfa_broker_xml_import",
                "bybit_spot_csv_bundle",
                "universal_broker",
            ],
        ),
    ],
    declared_format: Annotated[
        ImportFileFormat,
        Form(description="Uploaded file format matching the adapter", examples=["xlsx", "xml"]),
    ],
    file: Annotated[
        list[UploadFile],
        File(
            description=(
                "One report file, or all four Bybit Spot CSV exports as repeated `file` parts. "
                "Originals are stored outside the project tree."
            )
        ),
    ],
    session: Annotated[AsyncSession, Depends(get_db_session)],
    storage: Annotated[ObjectStorage, Depends(get_object_storage)],
) -> ImportUploadResponse:
    try:
        batch, duplicate = await create_import_batch_documents(
            session,
            storage,
            portfolio_id=portfolio_id,
            account_id=account_id,
            source_provider=source_provider,
            declared_format=declared_format,
            uploads=tuple(
                ImportUploadPart(
                    filename=item.filename,
                    content_type=item.content_type,
                    source=item.file,
                )
                for item in file
            ),
        )
    finally:
        for item in file:
            await item.close()
    if duplicate:
        response.status_code = status.HTTP_200_OK
    return ImportUploadResponse(
        batch=ImportBatchResponse.model_validate(batch),
        duplicate=duplicate,
    )


@router.get(
    "",
    response_model=ImportBatchListResponse,
    summary="List import batches",
    description=(
        "Returns newest import batches first for import history and recovery workflows. "
        "Optional portfolio, account, and status filters may be combined."
    ),
)
async def list_import_batches_route(
    session: Annotated[AsyncSession, Depends(get_db_session)],
    portfolio_id: Annotated[UUID | None, Query()] = None,
    account_id: Annotated[UUID | None, Query()] = None,
    import_status: Annotated[ImportStatus | None, Query(alias="status")] = None,
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> ImportBatchListResponse:
    batches = await list_import_batches(
        session,
        portfolio_id=portfolio_id,
        account_id=account_id,
        import_status=import_status,
        limit=limit,
        offset=offset,
    )
    return ImportBatchListResponse(
        items=[ImportBatchResponse.model_validate(batch) for batch in batches],
        limit=limit,
        offset=offset,
    )


@router.get(
    "/{batch_id}",
    response_model=ImportStatusResponse,
    responses=NOT_FOUND_RESPONSE,
    summary="Get import status and processing jobs",
    description=(
        "Returns batch counters and parse-job state. Poll this resource while the worker processes "
        "an uploaded file."
    ),
)
async def get_import_status_route(
    batch_id: UUID,
    session: Annotated[AsyncSession, Depends(get_db_session)],
) -> ImportStatusResponse:
    batch = await get_import_batch(session, batch_id)
    if batch is None:
        not_found("import_batch")
    return ImportStatusResponse(
        batch=ImportBatchResponse.model_validate(batch),
        jobs=[
            ImportJobResponse.model_validate(job)
            for job in sorted(batch.jobs, key=lambda item: (item.created_at, item.id))
        ],
    )


@router.get(
    "/{batch_id}/rows",
    response_model=ImportRowListResponse,
    responses=NOT_FOUND_RESPONSE,
    summary="List traceable import staging rows",
    description=(
        "Returns raw source data, normalized candidates, diagnostics, and review resolutions. "
        "These rows have not necessarily affected the ledger."
    ),
)
async def list_import_rows_route(
    batch_id: UUID,
    session: Annotated[AsyncSession, Depends(get_db_session)],
    limit: Annotated[int, Query(ge=1, le=200)] = 100,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> ImportRowListResponse:
    batch = await get_import_batch(session, batch_id)
    if batch is None:
        not_found("import_batch")
    rows = await list_import_rows(session, batch_id, limit=limit, offset=offset)
    return ImportRowListResponse(
        items=[ImportRowResponse.model_validate(row) for row in rows],
        limit=limit,
        offset=offset,
    )


@router.patch(
    "/{batch_id}/rows/{row_id}",
    response_model=ImportRowResponse,
    responses=WORKFLOW_RESPONSES,
    summary="Resolve or exclude an import staging row",
    description=(
        "Records an explicit review decision: exclude the row, map its instrument, or accept a "
        "known duplicate. The decision is retained for audit."
    ),
)
async def resolve_import_row_route(
    batch_id: UUID,
    row_id: UUID,
    payload: Annotated[
        ImportRowResolutionRequest,
        Body(openapi_examples=RESOLUTION_EXAMPLES),
    ],
    session: Annotated[AsyncSession, Depends(get_db_session)],
) -> ImportRowResponse:
    row = await resolve_import_row(
        session,
        batch_id=batch_id,
        row_id=row_id,
        request=payload,
    )
    return ImportRowResponse.model_validate(row)


@router.get(
    "/{batch_id}/preview",
    response_model=ImportPreviewResponse,
    responses=NOT_FOUND_RESPONSE,
    summary="Get import diagnostics and preview rows",
    description=(
        "Returns review counters, exact currency summaries, and a bounded row preview before "
        "ledger confirmation."
    ),
)
async def get_import_preview_route(
    batch_id: UUID,
    session: Annotated[AsyncSession, Depends(get_db_session)],
    limit: Annotated[int, Query(ge=1, le=200)] = 100,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> ImportPreviewResponse:
    batch = await get_import_batch(session, batch_id)
    if batch is None:
        not_found("import_batch")
    rows = await list_import_rows(session, batch_id, limit=limit, offset=offset)
    summary = await get_import_preview_summary(session, batch_id)
    return ImportPreviewResponse(
        batch_id=batch.id,
        source_provider=batch.source_provider,
        detected_format=batch.detected_format,
        detected_version=batch.detected_version,
        reporting_period_start=batch.reporting_period_start,
        reporting_period_end=batch.reporting_period_end,
        completeness=batch.completeness,
        reconciliation_status=batch.reconciliation_status,
        reconciliation_summary=batch.reconciliation_summary,
        status=batch.status,
        total_rows=batch.total_rows,
        ready_rows=batch.ready_rows,
        warning_rows=batch.warning_rows,
        error_rows=batch.error_rows,
        duplicate_rows=batch.duplicate_rows,
        excluded_rows=batch.excluded_rows,
        summary=summary,
        items=[ImportRowResponse.model_validate(row) for row in rows],
        limit=limit,
        offset=offset,
    )


@router.post(
    "/{batch_id}/confirm",
    response_model=ImportConfirmResponse,
    responses=WORKFLOW_RESPONSES,
    summary="Atomically confirm reviewed import rows",
    description=(
        "Validates every included row again and creates all ledger operations in one transaction. "
        "Repeating a successful confirm returns the same operation IDs."
    ),
)
async def confirm_import_route(
    batch_id: UUID,
    session: Annotated[AsyncSession, Depends(get_db_session)],
) -> ImportConfirmResponse:
    return await confirm_import(session, batch_id=batch_id)


@router.post(
    "/{batch_id}/rollback",
    response_model=ImportRollbackResponse,
    responses=WORKFLOW_RESPONSES,
    summary="Rollback only ledger operations created by this import",
    description=(
        "Idempotently removes ledger operations created by this batch and restores staging review. "
        "Manual operations and other batches are unchanged."
    ),
)
async def rollback_import_route(
    batch_id: UUID,
    session: Annotated[AsyncSession, Depends(get_db_session)],
) -> ImportRollbackResponse:
    return await rollback_import(session, batch_id=batch_id)


@formats_router.get(
    "",
    response_model=ImportFormatListResponse,
    summary="List registered versioned import adapters",
    description=(
        "Lists adapter IDs, versions, and file formats accepted by the current process, including "
        "tbank_broker_xlsx 1.0, alfa_broker_xml_import 1.0, and "
        "bybit_spot_csv_bundle 1.0."
    ),
)
async def list_import_formats_route(
    registry: Annotated[AdapterRegistry, Depends(get_adapter_registry)],
) -> ImportFormatListResponse:
    return ImportFormatListResponse(
        items=[
            ImportFormatResponse(
                format_id=descriptor.format_id,
                version=descriptor.version,
                supported_file_formats=list(descriptor.supported_file_formats),
            )
            for descriptor in list_import_formats(registry)
        ]
    )
