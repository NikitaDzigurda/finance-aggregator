"""Public CoinPaprika Free-plan snapshot for a curated set of crypto assets."""

from __future__ import annotations

import asyncio
import json
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime
from decimal import Decimal
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from pricing.market_data import MarketQuote, MarketSnapshot
from pricing.models import MarketPriceUnit

COINPAPRIKA_URL = "https://api.coinpaprika.com/v1/tickers?quotes=USD"
COINPAPRIKA_PROVIDER_ID = "coinpaprika"
COINPAPRIKA_MARKET = "GLOBAL"
COINPAPRIKA_PRICE_KIND = "global_aggregate"
COINPAPRIKA_TIME_QUALITY = "provider_snapshot"

# Reviewed exact asset code -> CoinPaprika coin id/name. This is deliberately
# narrower than the Free endpoint's 2,000 assets; a matching ticker is not enough.
COINPAPRIKA_ASSETS: dict[str, tuple[str, str]] = {
    "BTC": ("btc-bitcoin", "Bitcoin"),
    "ETH": ("eth-ethereum", "Ethereum"),
    "SOL": ("sol-solana", "Solana"),
    "USDT": ("usdt-tether", "Tether"),
    "USDC": ("usdc-usd-coin", "USDC"),
}

type JsonFetcher = Callable[[str, float, int], Awaitable[bytes]]


class CoinPaprikaProviderError(RuntimeError):
    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


class CoinPaprikaSnapshotProvider:
    provider_id = COINPAPRIKA_PROVIDER_ID
    market = COINPAPRIKA_MARKET

    def __init__(
        self,
        *,
        timeout_seconds: float,
        max_response_bytes: int,
        fetcher: JsonFetcher | None = None,
    ) -> None:
        self._timeout_seconds = timeout_seconds
        self._max_response_bytes = max_response_bytes
        self._fetcher = fetcher or fetch_public_json

    async def fetch_snapshot(self) -> MarketSnapshot:
        try:
            payload = await self._fetcher(
                COINPAPRIKA_URL, self._timeout_seconds, self._max_response_bytes
            )
        except (HTTPError, URLError, OSError, TimeoutError) as exc:
            raise CoinPaprikaProviderError("market_provider_unavailable") from exc
        return parse_coinpaprika_snapshot(payload, fetched_at=datetime.now(UTC))


async def fetch_public_json(url: str, timeout_seconds: float, max_bytes: int) -> bytes:
    return await asyncio.to_thread(_fetch_public_json_sync, url, timeout_seconds, max_bytes)


def _fetch_public_json_sync(url: str, timeout_seconds: float, max_bytes: int) -> bytes:
    if url != COINPAPRIKA_URL:
        raise CoinPaprikaProviderError("market_provider_url_invalid")
    request = Request(
        url,
        headers={
            "Accept": "application/json",
            "User-Agent": "finance-aggregator/0.1 public-market-data",
        },
        method="GET",
    )
    with urlopen(request, timeout=timeout_seconds) as response:  # noqa: S310
        if response.geturl() != COINPAPRIKA_URL:
            raise CoinPaprikaProviderError("market_provider_redirected")
        if response.headers.get_content_type() != "application/json":
            raise CoinPaprikaProviderError("market_provider_payload_invalid")
        payload = bytes(response.read(max_bytes + 1))
    if len(payload) > max_bytes:
        raise CoinPaprikaProviderError("market_provider_payload_too_large")
    return payload


def parse_coinpaprika_snapshot(payload: bytes, *, fetched_at: datetime) -> MarketSnapshot:
    if fetched_at.tzinfo is None:
        raise ValueError("fetched_at must be timezone-aware")
    try:
        rows = json.loads(payload, parse_float=Decimal)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise CoinPaprikaProviderError("market_provider_payload_invalid") from exc
    if not isinstance(rows, list) or not 1 <= len(rows) <= 2_000:
        raise CoinPaprikaProviderError("market_provider_snapshot_incomplete")
    by_id: dict[str, dict[str, object]] = {}
    for row in rows:
        if not isinstance(row, dict) or not isinstance(row.get("id"), str):
            raise CoinPaprikaProviderError("market_provider_payload_invalid")
        coin_id = row["id"]
        if coin_id in by_id:
            raise CoinPaprikaProviderError("market_provider_payload_invalid")
        by_id[coin_id] = row
    quotes: list[MarketQuote] = []
    for code, (coin_id, name) in COINPAPRIKA_ASSETS.items():
        row = by_id.get(coin_id)
        if row is None:
            continue  # Free-plan ranking may move an asset outside the top 2,000.
        if row.get("symbol") != code or row.get("name") != name:
            raise CoinPaprikaProviderError("market_provider_identity_changed")
        quote_values = row.get("quotes")
        if not isinstance(quote_values, dict):
            raise CoinPaprikaProviderError("market_provider_payload_invalid")
        usd = quote_values.get("USD")
        if not isinstance(usd, dict):
            raise CoinPaprikaProviderError("market_provider_payload_invalid")
        raw_price = usd.get("price")
        if isinstance(raw_price, bool) or not isinstance(raw_price, (int, Decimal)):
            raise CoinPaprikaProviderError("market_provider_payload_invalid")
        updated = row.get("last_updated")
        if not isinstance(updated, str):
            raise CoinPaprikaProviderError("market_provider_payload_invalid")
        try:
            observed_at = datetime.fromisoformat(updated.replace("Z", "+00:00"))
        except ValueError as exc:
            raise CoinPaprikaProviderError("market_provider_payload_invalid") from exc
        if observed_at.tzinfo is None:
            raise CoinPaprikaProviderError("market_provider_payload_invalid")
        quotes.append(
            MarketQuote(
                symbol=coin_id,
                quote_currency="USD",
                price_unit=MarketPriceUnit.CRYPTO_UNIT,
                price_kind=COINPAPRIKA_PRICE_KIND,
                time_quality=COINPAPRIKA_TIME_QUALITY,
                price=Decimal(raw_price),
                observed_at=observed_at.astimezone(UTC),
                fetched_at=fetched_at.astimezone(UTC),
            )
        )
    if not quotes:
        raise CoinPaprikaProviderError("market_provider_snapshot_incomplete")
    return MarketSnapshot(COINPAPRIKA_PROVIDER_ID, COINPAPRIKA_MARKET, tuple(quotes))
