from __future__ import annotations

from typing import Annotated, Literal

from fastapi import APIRouter, Depends, status
from pydantic import BaseModel
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession

from shared.database import check_database, get_db_session
from shared.errors import ApiErrorException, ErrorResponse

router = APIRouter(prefix="/health", tags=["health"])


class HealthResponse(BaseModel):
    """Health status returned by liveness and readiness probes."""

    status: Literal["ok"]


@router.get(
    "/live",
    response_model=HealthResponse,
    summary="Check whether the API process is alive",
)
async def live() -> HealthResponse:
    return HealthResponse(status="ok")


@router.get(
    "/ready",
    response_model=HealthResponse,
    responses={
        status.HTTP_503_SERVICE_UNAVAILABLE: {
            "model": ErrorResponse,
            "description": "PostgreSQL is not reachable",
        }
    },
    summary="Check whether the API can reach PostgreSQL",
)
async def ready(
    session: Annotated[AsyncSession, Depends(get_db_session)],
) -> HealthResponse:
    try:
        await check_database(session)
    except SQLAlchemyError as exc:
        raise ApiErrorException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            code="database_unavailable",
            message="Database is unavailable",
        ) from exc
    return HealthResponse(status="ok")
