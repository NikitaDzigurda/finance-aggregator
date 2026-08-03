from __future__ import annotations

from typing import NoReturn

from fastapi import status
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from shared.errors import ApiErrorException


def not_found(resource: str) -> NoReturn:
    """Raise the shared safe 404 envelope without echoing identifiers."""
    raise ApiErrorException(
        status_code=status.HTTP_404_NOT_FOUND,
        code=f"{resource}_not_found",
        message=f"{resource.replace('_', ' ').title()} was not found",
    )


async def commit_or_conflict(
    session: AsyncSession,
    *,
    code: str,
    message: str,
) -> None:
    """Commit one request transaction and translate integrity failures safely."""
    try:
        await session.commit()
    except IntegrityError as exc:
        await session.rollback()
        raise ApiErrorException(
            status_code=status.HTTP_409_CONFLICT,
            code=code,
            message=message,
        ) from exc
