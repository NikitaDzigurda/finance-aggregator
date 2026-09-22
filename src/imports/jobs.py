from __future__ import annotations

from datetime import UTC, datetime
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from imports.models import ImportJobModel, ImportJobStatus


async def claim_next_job(
    session: AsyncSession,
    *,
    worker_id: str,
) -> ImportJobModel | None:
    statement = (
        select(ImportJobModel)
        .where(
            ImportJobModel.status == ImportJobStatus.PENDING,
            ImportJobModel.available_at <= func.now(),
        )
        .order_by(ImportJobModel.available_at, ImportJobModel.created_at, ImportJobModel.id)
        .with_for_update(skip_locked=True)
        .limit(1)
    )
    job = await session.scalar(statement)
    if job is None:
        await session.rollback()
        return None
    job.status = ImportJobStatus.RUNNING
    job.attempts += 1
    job.locked_at = datetime.now(UTC)
    job.locked_by = worker_id
    job.last_error = None
    await session.commit()
    await session.refresh(job)
    return job


async def finish_job(
    session: AsyncSession,
    *,
    job_id: UUID,
    worker_id: str,
    succeeded: bool,
    error: dict[str, object] | None = None,
    result: dict[str, object] | None = None,
) -> None:
    statement = select(ImportJobModel).where(ImportJobModel.id == job_id).with_for_update()
    job = await session.scalar(statement)
    if job is None or job.status != ImportJobStatus.RUNNING or job.locked_by != worker_id:
        await session.rollback()
        raise RuntimeError("Import job is not owned by this worker")
    job.status = ImportJobStatus.SUCCEEDED if succeeded else ImportJobStatus.FAILED
    job.locked_at = None
    job.locked_by = None
    job.last_error = error
    if result is not None:
        job.payload = {**job.payload, "result": result}
    await session.commit()
