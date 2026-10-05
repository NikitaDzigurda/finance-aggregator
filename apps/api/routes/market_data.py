"""Safe read-only diagnostics for background public market data."""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from pricing.coinpaprika import COINPAPRIKA_MARKET, COINPAPRIKA_PROVIDER_ID
from pricing.market_data import get_latest_market_sync_job
from pricing.schemas import MarketSyncCountersResponse, MarketSyncJobResponse
from shared.database import get_db_session

router = APIRouter(prefix="/api/v1/market-data", tags=["pricing"])


@router.get(
    "/sync/latest",
    response_model=MarketSyncJobResponse | None,
    summary="Read the latest background crypto-price synchronization",
    description="Returns only safe status and counts, without market payloads or portfolio data.",
)
async def latest_market_sync_route(
    session: Annotated[AsyncSession, Depends(get_db_session)],
) -> MarketSyncJobResponse | None:
    job = await get_latest_market_sync_job(
        session, provider=COINPAPRIKA_PROVIDER_ID, market=COINPAPRIKA_MARKET
    )
    if job is None:
        return None
    error_code = None
    if isinstance(job.last_error, dict):
        value = job.last_error.get("code")
        if isinstance(value, str):
            error_code = value
    raw = job.payload.get("result")
    counters = None
    if isinstance(raw, dict):
        counters = MarketSyncCountersResponse.model_validate(raw)
    return MarketSyncJobResponse(
        id=job.id,
        provider=COINPAPRIKA_PROVIDER_ID,
        market=COINPAPRIKA_MARKET,
        status=job.status,
        attempts=job.attempts,
        created_at=job.created_at,
        updated_at=job.updated_at,
        error_code=error_code,
        counters=counters,
    )
