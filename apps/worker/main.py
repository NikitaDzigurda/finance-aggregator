from __future__ import annotations

import argparse
import asyncio
import os
import socket
from time import monotonic

import structlog

from imports.adapters import get_adapter_registry
from imports.jobs import claim_next_job, finish_job
from imports.models import ImportJobType
from imports.processor import ImportProcessingError, fail_import_batch, process_import_batch
from imports.storage import get_object_storage
from pricing.coinpaprika import CoinPaprikaProviderError
from pricing.fx import FxProviderError
from pricing.market_data import (
    MarketDataConflict,
    MarketProviderUnavailable,
    build_market_providers,
    enqueue_market_data_sync,
    synchronize_market_snapshot,
)
from pricing.service import build_fx_rate_provider, enqueue_fx_sync, synchronize_usd_rub
from shared.config import get_settings
from shared.database import async_session_factory, engine
from shared.model_registry import load_domain_models

logger = structlog.get_logger(__name__)

load_domain_models()


async def run_worker(*, once: bool) -> None:
    settings = get_settings()
    worker_id = f"{socket.gethostname()}:{os.getpid()}"
    storage = get_object_storage()
    registry = get_adapter_registry()
    fx_provider = build_fx_rate_provider(settings)
    market_providers = build_market_providers(settings)
    next_fx_check = 0.0
    next_market_check = 0.0
    try:
        while True:
            if (
                not once
                and settings.environment != "test"
                and settings.fx_auto_sync_enabled
                and monotonic() >= next_fx_check
            ):
                async with async_session_factory() as session:
                    await enqueue_fx_sync(
                        session,
                        minimum_interval_seconds=settings.fx_auto_sync_interval_seconds,
                    )
                next_fx_check = monotonic() + 60
            if (
                not once
                and settings.environment != "test"
                and settings.market_auto_sync_enabled
                and market_providers
                and monotonic() >= next_market_check
            ):
                for (provider_id, market), _provider in market_providers.items():
                    async with async_session_factory() as session:
                        await enqueue_market_data_sync(
                            session,
                            provider=provider_id,
                            market=market,
                            minimum_interval_seconds=settings.market_auto_sync_interval_seconds,
                        )
                next_market_check = monotonic() + 60
            async with async_session_factory() as session:
                job = await claim_next_job(session, worker_id=worker_id)
            if job is None:
                if once:
                    return
                await asyncio.sleep(settings.worker_poll_interval_seconds)
                continue

            error: dict[str, object] | None = None
            result: dict[str, object] | None = None
            try:
                if job.job_type == ImportJobType.FX_SYNC:
                    async with async_session_factory() as session:
                        await synchronize_usd_rub(session, fx_provider)
                elif job.job_type == ImportJobType.MARKET_DATA_SYNC:
                    provider_key = (
                        str(job.payload.get("provider")),
                        str(job.payload.get("market")),
                    )
                    market_provider = market_providers.get(provider_key)
                    if market_provider is None:
                        raise MarketProviderUnavailable()
                    market_result = await synchronize_market_snapshot(
                        async_session_factory, market_provider
                    )
                    result = {
                        "inserted": market_result.ingest.inserted,
                        "repeated": market_result.ingest.repeated,
                        "skipped_unmapped": market_result.ingest.skipped_unmapped,
                        "skipped_invalid": market_result.ingest.skipped_invalid,
                        "recalculated_portfolios": len(market_result.recalculated_portfolios),
                        "failed_portfolios": len(market_result.failed_portfolios),
                    }
                    if market_result.failed_portfolios:
                        error = {
                            "code": "market_recalculation_failed",
                            "message": "Prices were saved but portfolio calculation failed",
                        }
                elif job.job_type == ImportJobType.PARSE_IMPORT and job.batch_id is not None:
                    await process_import_batch(
                        async_session_factory,
                        storage,
                        registry,
                        batch_id=job.batch_id,
                    )
                else:
                    raise ImportProcessingError(
                        "import_job_type_unsupported",
                        "Worker does not support this job type yet",
                    )
            except ImportProcessingError as exc:
                error = {"code": exc.code, "message": exc.message}
                if job.batch_id is not None:
                    await fail_import_batch(
                        async_session_factory,
                        batch_id=job.batch_id,
                        code=exc.code,
                        message=exc.message,
                    )
                logger.warning(
                    "worker_job_failed",
                    job_id=str(job.id),
                    job_type=job.job_type.value,
                    code=exc.code,
                )
            except FxProviderError as exc:
                error = {"code": exc.code, "message": exc.message}
                logger.warning(
                    "fx_sync_job_failed",
                    job_id=str(job.id),
                    code=exc.code,
                )
            except CoinPaprikaProviderError as exc:
                error = {"code": exc.code, "message": "Market data provider failed"}
                logger.warning("market_data_sync_job_failed", job_id=str(job.id), code=exc.code)
            except (MarketDataConflict, MarketProviderUnavailable, ValueError) as exc:
                code = (
                    "market_provider_unavailable"
                    if isinstance(exc, MarketProviderUnavailable)
                    else "market_data_conflict"
                    if isinstance(exc, MarketDataConflict)
                    else "market_snapshot_invalid"
                )
                error = {"code": code, "message": "Market data sync failed"}
                logger.warning("market_data_sync_job_failed", job_id=str(job.id), code=code)
            except Exception as exc:
                is_import_job = job.batch_id is not None
                code = (
                    "import_processing_failed"
                    if is_import_job
                    else "market_data_sync_failed"
                    if job.job_type == ImportJobType.MARKET_DATA_SYNC
                    else "fx_sync_failed"
                )
                message = "Import processing failed" if is_import_job else "Background sync failed"
                error = {"code": code, "message": message}
                if job.batch_id is not None:
                    await fail_import_batch(
                        async_session_factory,
                        batch_id=job.batch_id,
                        code=code,
                        message=message,
                    )
                logger.error(
                    "worker_job_failed",
                    job_id=str(job.id),
                    job_type=job.job_type.value,
                    exception_type=type(exc).__name__,
                )

            async with async_session_factory() as session:
                await finish_job(
                    session,
                    job_id=job.id,
                    worker_id=worker_id,
                    succeeded=error is None,
                    error=error,
                    result=result,
                )
            if error is None:
                logger.info(
                    "worker_job_succeeded",
                    job_id=str(job.id),
                    job_type=job.job_type.value,
                )
            if once:
                return
    finally:
        await engine.dispose()


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--once", action="store_true")
    arguments = parser.parse_args()
    asyncio.run(run_worker(once=arguments.once))


if __name__ == "__main__":
    main()
