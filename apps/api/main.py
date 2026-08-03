from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI

from apps.api.routes.health import router as health_router
from shared.config import get_settings
from shared.database import engine


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
    )
    application.include_router(health_router)
    return application


app = create_app()
