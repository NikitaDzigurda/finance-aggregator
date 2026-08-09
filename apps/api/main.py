from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI

from apps.api.routes.accounts import router as accounts_router
from apps.api.routes.calculation import router as calculation_router
from apps.api.routes.health import router as health_router
from apps.api.routes.imports import formats_router as import_formats_router
from apps.api.routes.imports import router as imports_router
from apps.api.routes.instruments import router as instruments_router
from apps.api.routes.operations import router as operations_router
from apps.api.routes.portfolios import router as portfolios_router
from apps.api.routes.pricing import router as pricing_router
from shared.config import get_settings
from shared.database import engine
from shared.errors import default_error_responses, install_exception_handlers
from shared.model_registry import load_domain_models

load_domain_models()

OPENAPI_TAGS = [
    {
        "name": "health",
        "description": "Process liveness and PostgreSQL readiness probes.",
    },
    {
        "name": "portfolios",
        "description": "Top-level containers that define the reporting base currency.",
    },
    {
        "name": "accounts",
        "description": "Broker, bank, and CEX accounts owned by a portfolio.",
    },
    {
        "name": "instruments",
        "description": "Canonical assets with stable, scoped external identifiers.",
    },
    {
        "name": "operations",
        "description": "Immutable canonical ledger operations and correction entries.",
    },
    {
        "name": "pricing",
        "description": "Exact manual market-price observations used for valuation.",
    },
    {
        "name": "calculation",
        "description": "Deterministic portfolio positions, balances, and P&L snapshots.",
    },
    {
        "name": "imports",
        "description": "Traceable upload, staging review, confirm, and rollback workflow.",
    },
]


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
        description=(
            "Local backend for manually managed portfolios and traceable financial "
            "imports. Exact decimal values are JSON strings. Import data affects the "
            "ledger only after explicit confirmation."
        ),
        version="0.1.0",
        docs_url="/docs",
        openapi_url="/openapi.json",
        lifespan=lifespan,
        responses=default_error_responses(),
        openapi_tags=OPENAPI_TAGS,
    )
    install_exception_handlers(application)
    application.include_router(health_router)
    application.include_router(portfolios_router)
    application.include_router(accounts_router)
    application.include_router(instruments_router)
    application.include_router(operations_router)
    application.include_router(pricing_router)
    application.include_router(calculation_router)
    application.include_router(imports_router)
    application.include_router(import_formats_router)
    return application


app = create_app()
