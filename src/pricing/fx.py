from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime, time
from decimal import ROUND_HALF_EVEN, Context, Decimal, DecimalException, localcontext
from io import BytesIO
from typing import Literal, Protocol
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen
from uuid import UUID
from xml.etree import ElementTree
from zoneinfo import ZoneInfo

from imports.xml_security import XmlSecurityError, XmlSecurityLimits, validate_xml_document
from pricing.models import ExchangeRateMode
from shared.exact import RATE_SPEC, ExactDecimalError, validate_decimal

CBR_DAILY_URL = "https://www.cbr.ru/scripts/XML_daily.asp"
CBR_PROVIDER_ID = "cbr"
CBR_SOURCE_ID = "official_daily"
_CALCULATION_CONTEXT = Context(prec=100, rounding=ROUND_HALF_EVEN)
_RATE_QUANTUM = Decimal("0.000000000000000000000001")
_CBR_TIMEZONE = ZoneInfo("Europe/Moscow")
_CBR_XML_LIMITS = XmlSecurityLimits(
    max_size_bytes=512 * 1024,
    max_depth=16,
    max_elements=5_000,
    max_value_length=1_024,
    max_attributes_per_element=32,
)


class FxProviderError(RuntimeError):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


@dataclass(frozen=True, slots=True)
class FxRateObservation:
    base_currency: str
    quote_currency: str
    rate: Decimal
    observed_at: datetime
    fetched_at: datetime
    provider: str
    source: str
    mode: ExchangeRateMode


type FxHttpFetcher = Callable[[str, float, int], Awaitable[bytes]]


class FxRateProvider(Protocol):
    """Network-independent application boundary for public fiat FX observations."""

    provider_id: str
    source_id: str

    async def fetch_rate(
        self,
        *,
        base_currency: str,
        quote_currency: str,
    ) -> FxRateObservation:
        ...


class CbrDailyFxRateProvider(FxRateProvider):
    provider_id = CBR_PROVIDER_ID
    source_id = CBR_SOURCE_ID

    def __init__(
        self,
        *,
        timeout_seconds: float,
        max_attempts: int,
        retry_backoff_seconds: float,
        max_response_bytes: int,
        fetcher: FxHttpFetcher | None = None,
    ) -> None:
        self._timeout_seconds = timeout_seconds
        self._max_attempts = max_attempts
        self._retry_backoff_seconds = retry_backoff_seconds
        self._max_response_bytes = max_response_bytes
        self._fetcher = fetcher or _fetch_public_xml

    async def fetch_rate(
        self,
        *,
        base_currency: str,
        quote_currency: str,
    ) -> FxRateObservation:
        if (base_currency, quote_currency) != ("USD", "RUB"):
            raise FxProviderError(
                "fx_pair_unsupported",
                "The configured FX provider does not support this currency pair",
            )

        last_error: FxProviderError | None = None
        for attempt in range(1, self._max_attempts + 1):
            try:
                payload = await self._fetcher(
                    CBR_DAILY_URL,
                    self._timeout_seconds,
                    self._max_response_bytes,
                )
                return parse_cbr_daily_rate(
                    payload,
                    base_currency=base_currency,
                    quote_currency=quote_currency,
                    fetched_at=datetime.now(UTC),
                )
            except FxProviderError as exc:
                last_error = exc
            except (HTTPError, URLError, OSError, TimeoutError) as exc:
                last_error = FxProviderError(
                    "fx_provider_unavailable",
                    "Public FX provider is unavailable",
                )
                last_error.__cause__ = exc
            if attempt < self._max_attempts:
                await asyncio.sleep(self._retry_backoff_seconds * (2 ** (attempt - 1)))

        assert last_error is not None
        raise last_error


async def _fetch_public_xml(url: str, timeout_seconds: float, max_bytes: int) -> bytes:
    return await asyncio.to_thread(_fetch_public_xml_sync, url, timeout_seconds, max_bytes)


def _fetch_public_xml_sync(url: str, timeout_seconds: float, max_bytes: int) -> bytes:
    request = Request(
        url,
        headers={
            "Accept": "application/xml,text/xml;q=0.9",
            "User-Agent": "finance-aggregator/0.1 public-fx-sync",
        },
        method="GET",
    )
    with urlopen(request, timeout=timeout_seconds) as response:  # noqa: S310
        content_type = response.headers.get_content_type()
        if content_type not in {"application/xml", "text/xml", "application/octet-stream"}:
            raise FxProviderError(
                "fx_provider_payload_invalid",
                "Public FX provider returned an invalid response",
            )
        payload = bytes(response.read(max_bytes + 1))
    if len(payload) > max_bytes:
        raise FxProviderError(
            "fx_provider_payload_too_large",
            "Public FX provider response exceeds the configured limit",
        )
    return payload


def parse_cbr_daily_rate(
    payload: bytes,
    *,
    base_currency: str,
    quote_currency: str,
    fetched_at: datetime,
) -> FxRateObservation:
    if fetched_at.tzinfo is None or fetched_at.utcoffset() is None:
        raise ValueError("fetched_at must be timezone-aware")
    try:
        validate_xml_document(BytesIO(payload), limits=_CBR_XML_LIMITS)
        root = ElementTree.fromstring(payload)
        if root.tag != "ValCurs" or quote_currency != "RUB":
            raise ValueError
        date_text = root.attrib["Date"]
        effective_date = datetime.strptime(date_text, "%d.%m.%Y").date()
        matching = [
            item
            for item in root.findall("Valute")
            if (item.findtext("CharCode") or "").strip() == base_currency
        ]
        if len(matching) != 1:
            raise ValueError
        item = matching[0]
        nominal = _provider_decimal(item.findtext("Nominal"))
        value = _provider_decimal(item.findtext("Value"))
        if nominal <= 0 or value <= 0:
            raise ValueError
        with localcontext(_CALCULATION_CONTEXT):
            rate = validate_decimal(value / nominal, RATE_SPEC)
    except (
        DecimalException,
        ElementTree.ParseError,
        ExactDecimalError,
        KeyError,
        ValueError,
        XmlSecurityError,
    ) as exc:
        raise FxProviderError(
            "fx_provider_payload_invalid",
            "Public FX provider returned an invalid response",
        ) from exc

    observed_at = datetime.combine(effective_date, time.min, _CBR_TIMEZONE).astimezone(UTC)
    return FxRateObservation(
        base_currency=base_currency,
        quote_currency=quote_currency,
        rate=rate,
        observed_at=observed_at,
        fetched_at=fetched_at.astimezone(UTC),
        provider=CBR_PROVIDER_ID,
        source=CBR_SOURCE_ID,
        mode=ExchangeRateMode.AUTOMATIC,
    )


def _provider_decimal(value: str | None) -> Decimal:
    if value is None:
        raise ValueError
    normalized = value.strip().replace(",", ".")
    if not normalized or any(character not in "0123456789.-" for character in normalized):
        raise ValueError
    return Decimal(normalized)


@dataclass(frozen=True, slots=True)
class FxRateCandidate:
    id: UUID
    base_currency: str
    quote_currency: str
    rate: Decimal
    observed_at: datetime
    provider: str
    source: str
    mode: ExchangeRateMode


@dataclass(frozen=True, slots=True)
class FxRateUse:
    candidate: FxRateCandidate
    age_seconds: int


@dataclass(frozen=True, slots=True)
class ResolvedFxRate:
    base_currency: str
    quote_currency: str
    valuation_as_of: datetime
    status: Literal["fresh", "stale", "unavailable"]
    rate: Decimal | None
    path: Literal["identity", "direct", "inverse", "pivot", "unavailable"]
    observations: tuple[FxRateUse, ...]
    diagnostic_code: str | None


def resolve_fx_rate(
    candidates: Sequence[FxRateCandidate],
    *,
    base_currency: str,
    quote_currency: str,
    valuation_as_of: datetime,
    stale_after_seconds: int,
) -> ResolvedFxRate:
    """Resolve identity, direct, inverse, or one RUB-pivot path without graph search."""
    if valuation_as_of.tzinfo is None or valuation_as_of.utcoffset() is None:
        raise ValueError("valuation_as_of must be timezone-aware")
    as_of = valuation_as_of.astimezone(UTC)
    eligible = [item for item in candidates if item.observed_at <= as_of]
    if base_currency == quote_currency:
        return ResolvedFxRate(
            base_currency=base_currency,
            quote_currency=quote_currency,
            valuation_as_of=as_of,
            status="fresh",
            rate=Decimal(1),
            path="identity",
            observations=(),
            diagnostic_code=None,
        )

    direct = _latest(eligible, base_currency, quote_currency)
    if direct is not None:
        return _resolved(
            base_currency,
            quote_currency,
            as_of,
            "direct",
            direct.rate,
            (direct,),
            stale_after_seconds,
        )
    inverse = _latest(eligible, quote_currency, base_currency)
    if inverse is not None:
        return _resolved(
            base_currency,
            quote_currency,
            as_of,
            "inverse",
            _inverse(inverse.rate),
            (inverse,),
            stale_after_seconds,
        )

    if base_currency != "RUB" and quote_currency != "RUB":
        first = _leg(eligible, base_currency, "RUB")
        second = _leg(eligible, "RUB", quote_currency)
        if first is not None and second is not None:
            first_rate, first_observation = first
            second_rate, second_observation = second
            return _resolved(
                base_currency,
                quote_currency,
                as_of,
                "pivot",
                _multiply_rates(first_rate, second_rate),
                (first_observation, second_observation),
                stale_after_seconds,
            )

    return ResolvedFxRate(
        base_currency=base_currency,
        quote_currency=quote_currency,
        valuation_as_of=as_of,
        status="unavailable",
        rate=None,
        path="unavailable",
        observations=(),
        diagnostic_code="fx_rate_missing",
    )


def _latest(
    candidates: Sequence[FxRateCandidate], base_currency: str, quote_currency: str
) -> FxRateCandidate | None:
    matching = [
        item
        for item in candidates
        if item.base_currency == base_currency and item.quote_currency == quote_currency
    ]
    return max(matching, key=lambda item: (item.observed_at, str(item.id)), default=None)


def _leg(
    candidates: Sequence[FxRateCandidate], base_currency: str, quote_currency: str
) -> tuple[Decimal, FxRateCandidate] | None:
    direct = _latest(candidates, base_currency, quote_currency)
    if direct is not None:
        return direct.rate, direct
    inverse = _latest(candidates, quote_currency, base_currency)
    if inverse is not None:
        return _inverse(inverse.rate), inverse
    return None


def _inverse(rate: Decimal) -> Decimal:
    with localcontext(_CALCULATION_CONTEXT):
        return validate_decimal(
            (Decimal(1) / rate).quantize(_RATE_QUANTUM, rounding=ROUND_HALF_EVEN),
            RATE_SPEC,
        )


def _multiply_rates(first: Decimal, second: Decimal) -> Decimal:
    with localcontext(_CALCULATION_CONTEXT):
        return validate_decimal(
            (first * second).quantize(_RATE_QUANTUM, rounding=ROUND_HALF_EVEN),
            RATE_SPEC,
        )


def _resolved(
    base_currency: str,
    quote_currency: str,
    as_of: datetime,
    path: Literal["direct", "inverse", "pivot"],
    rate: Decimal,
    observations: tuple[FxRateCandidate, ...],
    stale_after_seconds: int,
) -> ResolvedFxRate:
    uses = tuple(
        FxRateUse(
            candidate=item,
            age_seconds=max(0, int((as_of - item.observed_at).total_seconds())),
        )
        for item in observations
    )
    stale = any(item.age_seconds > stale_after_seconds for item in uses)
    return ResolvedFxRate(
        base_currency=base_currency,
        quote_currency=quote_currency,
        valuation_as_of=as_of,
        status="stale" if stale else "fresh",
        rate=rate,
        path=path,
        observations=uses,
        diagnostic_code="fx_rate_stale" if stale else None,
    )
