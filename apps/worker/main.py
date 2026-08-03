from __future__ import annotations

import argparse
import asyncio
import os
import socket

import structlog

from imports.adapters import get_adapter_registry
from imports.jobs import claim_next_job, finish_job
from imports.models import ImportJobType
from imports.processor import ImportProcessingError, fail_import_batch, process_import_batch
from imports.storage import get_object_storage
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
    try:
        while True:
            async with async_session_factory() as session:
                job = await claim_next_job(session, worker_id=worker_id)
            if job is None:
                if once:
                    return
                await asyncio.sleep(settings.worker_poll_interval_seconds)
                continue

            error: dict[str, object] | None = None
            try:
                if job.job_type != ImportJobType.PARSE_IMPORT:
                    raise ImportProcessingError(
                        "import_job_type_unsupported",
                        "Worker does not support this job type yet",
                    )
                await process_import_batch(
                    async_session_factory,
                    storage,
                    registry,
                    batch_id=job.batch_id,
                )
            except ImportProcessingError as exc:
                error = {"code": exc.code, "message": exc.message}
                await fail_import_batch(
                    async_session_factory,
                    batch_id=job.batch_id,
                    code=exc.code,
                    message=exc.message,
                )
                logger.warning(
                    "import_job_failed",
                    job_id=str(job.id),
                    batch_id=str(job.batch_id),
                    code=exc.code,
                )
            except Exception as exc:
                error = {
                    "code": "import_processing_failed",
                    "message": "Import processing failed",
                }
                await fail_import_batch(
                    async_session_factory,
                    batch_id=job.batch_id,
                    code="import_processing_failed",
                    message="Import processing failed",
                )
                logger.error(
                    "import_job_failed",
                    job_id=str(job.id),
                    batch_id=str(job.batch_id),
                    exception_type=type(exc).__name__,
                )

            async with async_session_factory() as session:
                await finish_job(
                    session,
                    job_id=job.id,
                    worker_id=worker_id,
                    succeeded=error is None,
                    error=error,
                )
            if error is None:
                logger.info(
                    "import_job_succeeded",
                    job_id=str(job.id),
                    batch_id=str(job.batch_id),
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
