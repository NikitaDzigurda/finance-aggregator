from __future__ import annotations

from collections.abc import Mapping
from http import HTTPStatus
from typing import Any

import structlog
from fastapi import FastAPI, HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field
from starlette.exceptions import HTTPException as StarletteHTTPException

logger = structlog.get_logger(__name__)


class ApiErrorDetail(BaseModel):
    """One safe, machine-readable API error detail."""

    model_config = ConfigDict(extra="forbid")

    location: list[str | int] = Field(default_factory=list)
    code: str
    message: str


class ApiError(BaseModel):
    """Stable application error independent of framework exception shapes."""

    model_config = ConfigDict(extra="forbid")

    code: str
    message: str
    details: list[ApiErrorDetail] = Field(default_factory=list)


class ErrorResponse(BaseModel):
    """Envelope returned for all HTTP and validation errors."""

    model_config = ConfigDict(extra="forbid")

    error: ApiError


class ApiErrorException(HTTPException):
    """HTTP exception with an explicit stable application error code."""

    def __init__(
        self,
        *,
        status_code: int,
        code: str,
        message: str,
        details: list[ApiErrorDetail] | None = None,
        headers: dict[str, str] | None = None,
    ) -> None:
        super().__init__(status_code=status_code, detail=message, headers=headers)
        self.code = code
        self.message = message
        self.details = details or []


def _response(
    *,
    status_code: int,
    code: str,
    message: str,
    details: list[ApiErrorDetail] | None = None,
    headers: Mapping[str, str] | None = None,
) -> JSONResponse:
    body = ErrorResponse(
        error=ApiError(code=code, message=message, details=details or []),
    )
    return JSONResponse(
        status_code=status_code,
        content=body.model_dump(mode="json"),
        headers=headers,
    )


async def validation_exception_handler(
    request: Request,
    exc: RequestValidationError,
) -> JSONResponse:
    del request
    details = [
        ApiErrorDetail(
            location=[part for part in error.get("loc", ()) if isinstance(part, (str, int))],
            code=str(error.get("type", "validation_error")),
            message=str(error.get("msg", "Invalid value")),
        )
        for error in exc.errors()
    ]
    return _response(
        status_code=422,
        code="request_validation_error",
        message="Request validation failed",
        details=details,
    )


async def http_exception_handler(
    request: Request,
    exc: StarletteHTTPException,
) -> JSONResponse:
    del request
    if isinstance(exc, ApiErrorException):
        return _response(
            status_code=exc.status_code,
            code=exc.code,
            message=exc.message,
            details=exc.details,
            headers=exc.headers,
        )

    fallback_message = HTTPStatus(exc.status_code).phrase
    message = exc.detail if isinstance(exc.detail, str) else fallback_message
    return _response(
        status_code=exc.status_code,
        code="http_error",
        message=message,
        headers=exc.headers,
    )


async def unhandled_exception_handler(request: Request, exc: Exception) -> JSONResponse:
    logger.error(
        "unhandled_api_exception",
        method=request.method,
        path=request.url.path,
        exception_type=type(exc).__name__,
    )
    return _response(
        status_code=500,
        code="internal_error",
        message="Internal server error",
    )


def install_exception_handlers(application: FastAPI) -> None:
    """Install one response envelope without exposing request inputs in errors."""
    application.add_exception_handler(
        RequestValidationError,
        validation_exception_handler,  # type: ignore[arg-type]
    )
    application.add_exception_handler(
        StarletteHTTPException,
        http_exception_handler,  # type: ignore[arg-type]
    )
    application.add_exception_handler(
        Exception,
        unhandled_exception_handler,
    )


def default_error_responses() -> dict[int | str, dict[str, Any]]:
    """OpenAPI responses inherited by all application routes."""
    return {
        422: {
            "model": ErrorResponse,
            "description": "Request validation failed",
        },
        500: {
            "model": ErrorResponse,
            "description": "Unexpected internal error",
        },
    }
