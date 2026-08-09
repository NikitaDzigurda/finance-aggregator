from __future__ import annotations

from typing import Annotated, Any
from uuid import UUID

from fastapi import APIRouter, Body, Depends, status
from sqlalchemy.ext.asyncio import AsyncSession

from calculation.schemas import PositionSnapshotResponse, RecalculatePositionsRequest
from calculation.service import (
    get_position_snapshot,
    position_snapshot_response,
    recalculate_portfolio,
)
from shared.database import get_db_session
from shared.errors import ApiErrorException, ErrorResponse

router = APIRouter(prefix="/api/v1/portfolios", tags=["calculation"])

WORKFLOW_RESPONSES: dict[int | str, dict[str, Any]] = {
    status.HTTP_404_NOT_FOUND: {
        "model": ErrorResponse,
        "description": "Portfolio or calculated position snapshot was not found",
    },
    status.HTTP_409_CONFLICT: {
        "model": ErrorResponse,
        "description": "Exact portfolio calculation could not be persisted",
    },
}
RECALCULATION_EXAMPLES = {
    "as_of": {
        "summary": "Weighted-average snapshot at a fixed instant",
        "value": {
            "as_of": "2026-08-10T23:59:59Z",
            "cost_basis_method": "weighted_average",
        },
    },
    "latest": {
        "summary": "Use the current instant and default policy",
        "value": {},
    },
}


@router.post(
    "/{portfolio_id}/positions/recalculate",
    response_model=PositionSnapshotResponse,
    responses=WORKFLOW_RESPONSES,
    summary="Recalculate and replace the portfolio position snapshot",
    description=(
        "Replays immutable ledger operations through the requested instant, selects eligible "
        "manual prices, and atomically replaces the derived snapshot. The ledger is unchanged."
    ),
)
async def recalculate_positions_route(
    portfolio_id: UUID,
    payload: Annotated[
        RecalculatePositionsRequest,
        Body(openapi_examples=RECALCULATION_EXAMPLES),
    ],
    session: Annotated[AsyncSession, Depends(get_db_session)],
) -> PositionSnapshotResponse:
    snapshot = await recalculate_portfolio(
        session,
        portfolio_id=portfolio_id,
        as_of=payload.as_of,
        cost_basis_method=payload.cost_basis_method,
    )
    return position_snapshot_response(snapshot)


@router.get(
    "/{portfolio_id}/positions",
    response_model=PositionSnapshotResponse,
    responses=WORKFLOW_RESPONSES,
    summary="Get the latest calculated portfolio position snapshot",
    description=(
        "Returns the last persisted snapshot, including exact positions, cash, separate metrics, "
        "and diagnostics. It does not trigger recalculation."
    ),
)
async def get_positions_route(
    portfolio_id: UUID,
    session: Annotated[AsyncSession, Depends(get_db_session)],
) -> PositionSnapshotResponse:
    snapshot = await get_position_snapshot(session, portfolio_id)
    if snapshot is None:
        raise ApiErrorException(
            status_code=status.HTTP_404_NOT_FOUND,
            code="position_snapshot_not_found",
            message="Position snapshot was not found",
        )
    return position_snapshot_response(snapshot)
