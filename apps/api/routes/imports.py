from __future__ import annotations

from typing import Annotated, Any
from uuid import UUID

from fastapi import APIRouter, Depends, File, Form, Query, Response, UploadFile, status
from sqlalchemy.ext.asyncio import AsyncSession

from apps.api.routes.common import not_found
from imports.adapters import AdapterRegistry, get_adapter_registry
from imports.models import ImportFileFormat
from imports.schemas import (
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
    create_import_batch,
    get_import_batch,
    get_import_preview_summary,
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


@router.post(
    "",
    response_model=ImportUploadResponse,
    status_code=status.HTTP_201_CREATED,
    responses=UPLOAD_RESPONSES,
    summary="Upload an import file into traceable staging",
)
async def upload_import_route(
    response: Response,
    portfolio_id: Annotated[UUID, Form()],
    account_id: Annotated[UUID, Form()],
    source_provider: Annotated[SourceProvider, Form()],
    declared_format: Annotated[ImportFileFormat, Form()],
    file: Annotated[
        UploadFile,
        File(description="CSV, XLSX, or PDF report stored outside the project tree"),
    ],
    session: Annotated[AsyncSession, Depends(get_db_session)],
    storage: Annotated[ObjectStorage, Depends(get_object_storage)],
) -> ImportUploadResponse:
    try:
        batch, duplicate = await create_import_batch(
            session,
            storage,
            portfolio_id=portfolio_id,
            account_id=account_id,
            source_provider=source_provider,
            declared_format=declared_format,
            filename=file.filename,
            content_type=file.content_type,
            source=file.file,
        )
    finally:
        await file.close()
    if duplicate:
        response.status_code = status.HTTP_200_OK
    return ImportUploadResponse(
        batch=ImportBatchResponse.model_validate(batch),
        duplicate=duplicate,
    )


@router.get(
    "/{batch_id}",
    response_model=ImportStatusResponse,
    responses=NOT_FOUND_RESPONSE,
    summary="Get import status and processing jobs",
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
)
async def resolve_import_row_route(
    batch_id: UUID,
    row_id: UUID,
    payload: ImportRowResolutionRequest,
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
