from datetime import UTC, datetime
from decimal import Decimal
from urllib.error import HTTPError, URLError
from uuid import uuid4

import pytest

from pricing.fx import (
    CbrDailyFxRateProvider,
    FxProviderError,
    FxRateCandidate,
    parse_cbr_daily_rate,
    resolve_fx_rate,
)
from pricing.models import ExchangeRateMode

CBR_XML = b"""<?xml version="1.0" encoding="UTF-8"?>
<ValCurs Date="22.08.2026" name="Foreign Currency Market">
  <Valute ID="R01235">
    <NumCode>840</NumCode><CharCode>USD</CharCode><Nominal>1</Nominal>
    <Name>US Dollar</Name><Value>80,1250</Value><VunitRate>80,1250</VunitRate>
  </Valute>
</ValCurs>
"""


def _candidate(
    base_currency: str,
    quote_currency: str,
    rate: str,
    observed_at: datetime,
) -> FxRateCandidate:
    return FxRateCandidate(
        id=uuid4(),
        base_currency=base_currency,
        quote_currency=quote_currency,
        rate=Decimal(rate),
        observed_at=observed_at,
        provider="manual",
        source="test",
        mode=ExchangeRateMode.MANUAL,
    )


def test_cbr_daily_payload_has_exact_rate_and_moscow_effective_time() -> None:
    result = parse_cbr_daily_rate(
        CBR_XML,
        base_currency="USD",
        quote_currency="RUB",
        fetched_at=datetime(2026, 8, 21, 18, 0, tzinfo=UTC),
    )

    assert result.rate == Decimal("80.1250")
    assert result.observed_at == datetime(2026, 8, 21, 21, 0, tzinfo=UTC)
    assert result.fetched_at == datetime(2026, 8, 21, 18, 0, tzinfo=UTC)
    assert result.mode is ExchangeRateMode.AUTOMATIC


def test_resolver_ignores_future_and_supports_direct_inverse_and_one_rub_pivot() -> None:
    old = datetime(2026, 8, 20, tzinfo=UTC)
    future = datetime(2026, 8, 23, tzinfo=UTC)
    as_of = datetime(2026, 8, 22, tzinfo=UTC)
    rates = [
        _candidate("USD", "RUB", "80", old),
        _candidate("USD", "RUB", "99", future),
        _candidate("EUR", "RUB", "100", old),
    ]

    direct = resolve_fx_rate(
        rates,
        base_currency="USD",
        quote_currency="RUB",
        valuation_as_of=as_of,
        stale_after_seconds=3 * 24 * 60 * 60,
    )
    inverse = resolve_fx_rate(
        rates,
        base_currency="RUB",
        quote_currency="USD",
        valuation_as_of=as_of,
        stale_after_seconds=3 * 24 * 60 * 60,
    )
    pivot = resolve_fx_rate(
        rates,
        base_currency="EUR",
        quote_currency="USD",
        valuation_as_of=as_of,
        stale_after_seconds=3 * 24 * 60 * 60,
    )
    missing = resolve_fx_rate(
        rates,
        base_currency="GBP",
        quote_currency="USD",
        valuation_as_of=as_of,
        stale_after_seconds=3 * 24 * 60 * 60,
    )

    assert direct.rate == Decimal("80")
    assert inverse.rate == Decimal("0.012500000000000000000000")
    assert pivot.rate == Decimal("1.250000000000000000000000")
    assert missing.rate is None
    assert missing.diagnostic_code == "fx_rate_missing"


@pytest.mark.parametrize(
    "failure",
    [
        TimeoutError(),
        URLError("synthetic unavailable"),
        HTTPError("https://www.cbr.ru", 503, "synthetic", {}, None),
        b"<ValCurs Date='22.08.2026'><Valute></ValCurs>",
        CBR_XML.replace(b"80,1250", b"-1", 1),
        b"<!DOCTYPE x [<!ENTITY e SYSTEM 'https://example.invalid'>]><ValCurs/>",
    ],
)
@pytest.mark.asyncio
async def test_provider_failures_return_only_safe_diagnostics(failure: object) -> None:
    async def fetcher(_url: str, _timeout: float, _maximum: int) -> bytes:
        if isinstance(failure, BaseException):
            raise failure
        assert isinstance(failure, bytes)
        return failure

    provider = CbrDailyFxRateProvider(
        timeout_seconds=0.1,
        max_attempts=1,
        retry_backoff_seconds=0,
        max_response_bytes=1024,
        fetcher=fetcher,
    )

    with pytest.raises(FxProviderError) as raised:
        await provider.fetch_rate(base_currency="USD", quote_currency="RUB")

    assert raised.value.code in {
        "fx_provider_unavailable",
        "fx_provider_payload_invalid",
    }
    assert "synthetic" not in raised.value.message
