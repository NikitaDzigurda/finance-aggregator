from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI

from apps.api.routes.accounts import router as accounts_router
from apps.api.routes.health import router as health_router
from apps.api.routes.instruments import router as instruments_router
from apps.api.routes.portfolios import router as portfolios_router
from shared.config import get_settings
from shared.database import engine
from shared.errors import default_error_responses, install_exception_handlers


@asynccontextmanager
async def lifespan(_: FastAPI) -> AsyncIterator[None]:
    """Release pooled database connections during application shutdown."""
    yield
    await engine.dispose()


def create_app() -> FastAPI:
    """Build the FastAPI application without performing network I/O."""
    settings = get_settings()
    application = FastAPI(
        title=settings.app_name,
        summary="Backend API for traceable personal finance aggregation.",
        version="0.1.0",
        docs_url="/docs",
        openapi_url="/openapi.json",
        lifespan=lifespan,
        responses=default_error_responses(),
    )
    install_exception_handlers(application)
    application.include_router(health_router)
    application.include_router(portfolios_router)
    application.include_router(accounts_router)
    application.include_router(instruments_router)
    return application


app = create_app()
