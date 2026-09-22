from __future__ import annotations

from dataclasses import replace
from uuid import UUID

from pydantic import TypeAdapter, ValidationError
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from imports.adapters import ParsedRow
from instruments.models import (
    InstrumentIdentifierModel,
    InstrumentIdentifierType,
    InstrumentModel,
    InstrumentType,
)
from operations.schemas import OperationCreate

type Diagnostic = dict[str, object]
_OPERATION_ADAPTER: TypeAdapter[OperationCreate] = TypeAdapter(OperationCreate)


async def resolve_import_instruments(
    session: AsyncSession,
    rows: tuple[ParsedRow, ...],
    *,
    portfolio_id: UUID,
    account_id: UUID,
) -> tuple[ParsedRow, ...]:
    references = [_references_from_row(row) for row in rows]
    flat_references = [
        item[2]
        for row, row_references in zip(rows, references, strict=True)
        if not row.errors
        for item in row_references
    ]
    isins = {
        value
        for reference in flat_references
        if isinstance((value := reference.get("isin")), str)
    }
    tickers = {
        value
        for reference in flat_references
        if isinstance((value := reference.get("ticker")), str)
    }
    provider_codes = {
        value
        for reference in flat_references
        if isinstance((value := reference.get("provider_code")), str)
    }
    crypto_asset_codes = {
        value
        for reference in flat_references
        if isinstance((value := reference.get("crypto_asset_code")), str)
    }
    isin_matches = await _load_isin_matches(session, isins)
    ticker_matches = await _load_ticker_matches(session, tickers)
    provider_matches = await _load_provider_matches(session, provider_codes)
    crypto_matches = await _load_crypto_matches(session, crypto_asset_codes)
    await _auto_create_instruments(
        session,
        flat_references,
        isin_matches=isin_matches,
        provider_matches=provider_matches,
        crypto_matches=crypto_matches,
    )

    resolved: list[ParsedRow] = []
    for row, row_references in zip(rows, references, strict=True):
        candidate = row.normalized_candidate
        warnings = list(row.warnings)
        errors = list(row.errors)
        if candidate is None:
            resolved.append(row)
            continue

        candidate_copy = dict(candidate)
        payload = candidate_copy.get("payload")
        if not isinstance(payload, dict):
            errors.append(
                {
                    "code": "operation_payload_invalid",
                    "message": "Normalized candidate payload is invalid",
                }
            )
            resolved.append(
                replace(
                    row,
                    normalized_candidate=None,
                    warnings=tuple(warnings),
                    errors=tuple(errors),
                )
            )
            continue

        payload_copy = dict(payload)
        unresolved = False
        for reference_key, instrument_key, reference in row_references:
            instrument_id, match_warnings, match_errors = _match_reference(
                reference,
                isin_matches,
                ticker_matches,
                provider_matches,
                crypto_matches,
            )
            target = reference_key.removesuffix("_reference")
            if target != "instrument":
                for diagnostic in [*match_warnings, *match_errors]:
                    diagnostic["target"] = target
            warnings.extend(match_warnings)
            errors.extend(match_errors)
            if instrument_id is None:
                unresolved = True
                continue
            payload_copy.pop(reference_key, None)
            payload_copy[instrument_key] = str(instrument_id)

        candidate_copy["payload"] = payload_copy
        if unresolved:
            resolved.append(
                replace(
                    row,
                    normalized_candidate=candidate_copy,
                    warnings=tuple(warnings),
                    errors=tuple(errors),
                )
            )
            continue

        validated, validation_errors = _validate_candidate(
            candidate_copy,
            portfolio_id=portfolio_id,
            account_id=account_id,
        )
        errors.extend(validation_errors)
        resolved.append(
            replace(
                row,
                normalized_candidate=validated,
                warnings=tuple(warnings),
                errors=tuple(errors),
            )
        )
    return tuple(resolved)


async def _auto_create_instruments(
    session: AsyncSession,
    references: list[dict[str, object]],
    *,
    isin_matches: dict[str, UUID],
    provider_matches: dict[tuple[str, str], UUID],
    crypto_matches: dict[str, UUID],
) -> None:
    grouped: dict[tuple[str, str, str | None], list[dict[str, object]]] = {}
    for reference in references:
        if reference.get("auto_create") is not True:
            continue
        identity = _reference_identity(reference)
        if identity is None or _reference_has_match(
            reference,
            isin_matches=isin_matches,
            provider_matches=provider_matches,
            crypto_matches=crypto_matches,
        ):
            continue
        grouped.setdefault(identity, []).append(reference)

    created: list[tuple[InstrumentModel, tuple[InstrumentIdentifierModel, ...]]] = []
    for candidates in grouped.values():
        metadata = {
            (
                item.get("name"),
                item.get("instrument_type"),
                item.get("currency"),
            )
            for item in candidates
        }
        if len(metadata) != 1:
            continue
        name, raw_type, currency = next(iter(metadata))
        if (
            not isinstance(name, str)
            or not name.strip()
            or len(name.strip()) > 200
            or not isinstance(raw_type, str)
            or not isinstance(currency, str)
            or len(currency) != 3
            or not currency.isascii()
            or not currency.isalpha()
            or not currency.isupper()
        ):
            continue
        try:
            instrument_type = InstrumentType(raw_type)
        except ValueError:
            continue
        identifiers = _auto_identifiers(candidates)
        if not identifiers:
            continue
        instrument = InstrumentModel(
            name=name.strip(),
            instrument_type=instrument_type,
            currency=currency,
        )
        instrument.identifiers = list(identifiers)
        session.add(instrument)
        created.append((instrument, identifiers))

    if not created:
        return
    await session.flush()
    for instrument, identifiers in created:
        for identifier in identifiers:
            if identifier.identifier_type is InstrumentIdentifierType.ISIN:
                isin_matches[identifier.value] = instrument.id
            elif identifier.identifier_type is InstrumentIdentifierType.PROVIDER_CODE:
                assert identifier.provider is not None
                provider_matches[(identifier.provider, identifier.value)] = instrument.id
            elif identifier.identifier_type is InstrumentIdentifierType.CRYPTO_ASSET_CODE:
                crypto_matches[identifier.value] = instrument.id


def _reference_identity(
    reference: dict[str, object],
) -> tuple[str, str, str | None] | None:
    crypto_code = reference.get("crypto_asset_code")
    if isinstance(crypto_code, str):
        return ("crypto_asset_code", crypto_code, None)
    isin = reference.get("isin")
    if isinstance(isin, str):
        return ("isin", isin, None)
    provider = reference.get("provider")
    provider_code = reference.get("provider_code")
    if isinstance(provider, str) and isinstance(provider_code, str):
        return ("provider_code", provider_code, provider)
    return None


def _reference_has_match(
    reference: dict[str, object],
    *,
    isin_matches: dict[str, UUID],
    provider_matches: dict[tuple[str, str], UUID],
    crypto_matches: dict[str, UUID],
) -> bool:
    crypto_code = reference.get("crypto_asset_code")
    if isinstance(crypto_code, str) and crypto_code in crypto_matches:
        return True
    isin = reference.get("isin")
    if isinstance(isin, str) and isin in isin_matches:
        return True
    provider = reference.get("provider")
    provider_code = reference.get("provider_code")
    return (
        isinstance(provider, str)
        and isinstance(provider_code, str)
        and (provider, provider_code) in provider_matches
    )


def _auto_identifiers(
    references: list[dict[str, object]],
) -> tuple[InstrumentIdentifierModel, ...]:
    result: list[InstrumentIdentifierModel] = []
    seen: set[tuple[str, str, str | None]] = set()
    for reference in references:
        isin = reference.get("isin")
        if isinstance(isin, str) and ("isin", isin, None) not in seen:
            seen.add(("isin", isin, None))
            result.append(
                InstrumentIdentifierModel(
                    identifier_type=InstrumentIdentifierType.ISIN,
                    value=isin,
                )
            )
        provider = reference.get("provider")
        provider_code = reference.get("provider_code")
        if (
            isinstance(provider, str)
            and isinstance(provider_code, str)
            and ("provider_code", provider_code, provider) not in seen
        ):
            seen.add(("provider_code", provider_code, provider))
            result.append(
                InstrumentIdentifierModel(
                    identifier_type=InstrumentIdentifierType.PROVIDER_CODE,
                    value=provider_code,
                    provider=provider,
                )
            )
        crypto_code = reference.get("crypto_asset_code")
        if (
            isinstance(crypto_code, str)
            and ("crypto_asset_code", crypto_code, None) not in seen
        ):
            seen.add(("crypto_asset_code", crypto_code, None))
            result.append(
                InstrumentIdentifierModel(
                    identifier_type=InstrumentIdentifierType.CRYPTO_ASSET_CODE,
                    value=crypto_code,
                )
            )
    return tuple(result)


def _references_from_row(
    row: ParsedRow,
) -> tuple[tuple[str, str, dict[str, object]], ...]:
    candidate = row.normalized_candidate
    if candidate is None:
        return ()
    payload = candidate.get("payload")
    if not isinstance(payload, dict):
        return ()
    result: list[tuple[str, str, dict[str, object]]] = []
    for reference_key, instrument_key in (
        ("instrument_reference", "instrument_id"),
        ("sold_instrument_reference", "sold_instrument_id"),
        ("bought_instrument_reference", "bought_instrument_id"),
    ):
        reference = payload.get(reference_key)
        if isinstance(reference, dict):
            result.append((reference_key, instrument_key, reference))
    return tuple(result)


async def _load_isin_matches(
    session: AsyncSession,
    values: set[str],
) -> dict[str, UUID]:
    if not values:
        return {}
    result = await session.execute(
        select(InstrumentIdentifierModel.value, InstrumentIdentifierModel.instrument_id).where(
            InstrumentIdentifierModel.identifier_type == InstrumentIdentifierType.ISIN,
            InstrumentIdentifierModel.value.in_(values),
        )
    )
    return {value: instrument_id for value, instrument_id in result.all()}


async def _load_ticker_matches(
    session: AsyncSession,
    values: set[str],
) -> dict[tuple[str, str, str], UUID]:
    if not values:
        return {}
    result = await session.execute(
        select(
            InstrumentIdentifierModel.value,
            InstrumentIdentifierModel.exchange,
            InstrumentModel.currency,
            InstrumentIdentifierModel.instrument_id,
        )
        .join(InstrumentModel, InstrumentModel.id == InstrumentIdentifierModel.instrument_id)
        .where(
            InstrumentIdentifierModel.identifier_type == InstrumentIdentifierType.TICKER,
            InstrumentIdentifierModel.value.in_(values),
        )
    )
    return {
        (ticker, exchange, currency): instrument_id
        for ticker, exchange, currency, instrument_id in result.all()
        if exchange is not None
    }


async def _load_provider_matches(
    session: AsyncSession,
    values: set[str],
) -> dict[tuple[str, str], UUID]:
    if not values:
        return {}
    result = await session.execute(
        select(
            InstrumentIdentifierModel.value,
            InstrumentIdentifierModel.provider,
            InstrumentIdentifierModel.instrument_id,
        ).where(
            InstrumentIdentifierModel.identifier_type == InstrumentIdentifierType.PROVIDER_CODE,
            InstrumentIdentifierModel.value.in_(values),
        )
    )
    return {
        (provider, value): instrument_id
        for value, provider, instrument_id in result.all()
        if provider is not None
    }


async def _load_crypto_matches(
    session: AsyncSession,
    values: set[str],
) -> dict[str, UUID]:
    if not values:
        return {}
    result = await session.execute(
        select(
            InstrumentIdentifierModel.value,
            InstrumentIdentifierModel.instrument_id,
        ).where(
            InstrumentIdentifierModel.identifier_type
            == InstrumentIdentifierType.CRYPTO_ASSET_CODE,
            InstrumentIdentifierModel.value.in_(values),
        )
    )
    return {value: instrument_id for value, instrument_id in result.all()}


def _match_reference(
    reference: dict[str, object],
    isin_matches: dict[str, UUID],
    ticker_matches: dict[tuple[str, str, str], UUID],
    provider_matches: dict[tuple[str, str], UUID],
    crypto_matches: dict[str, UUID],
) -> tuple[UUID | None, list[Diagnostic], list[Diagnostic]]:
    warnings: list[Diagnostic] = []
    errors: list[Diagnostic] = []
    isin = reference.get("isin")
    ticker = reference.get("ticker")
    exchange = reference.get("exchange")
    currency = reference.get("currency")
    provider = reference.get("provider")
    provider_code = reference.get("provider_code")
    crypto_asset_code = reference.get("crypto_asset_code")
    isin_match = isin_matches.get(isin) if isinstance(isin, str) else None
    ticker_match = (
        ticker_matches.get((ticker, exchange, currency))
        if isinstance(ticker, str)
        and isinstance(exchange, str)
        and isinstance(currency, str)
        else None
    )
    provider_match = (
        provider_matches.get((provider, provider_code))
        if isinstance(provider, str) and isinstance(provider_code, str)
        else None
    )
    crypto_match = (
        crypto_matches.get(crypto_asset_code)
        if isinstance(crypto_asset_code, str)
        else None
    )

    if crypto_match is not None:
        return crypto_match, warnings, errors

    if isin_match is not None:
        other_matches = {item for item in (ticker_match, provider_match) if item is not None}
        if any(item != isin_match for item in other_matches):
            errors.append(
                {
                    "code": "instrument_reference_conflict",
                    "message": "ISIN and secondary identifiers match different instruments",
                }
            )
            return None, warnings, errors
        if isinstance(ticker, str) and ticker_match is None:
            warnings.append(
                {
                    "code": "instrument_ticker_mismatch",
                    "message": "Instrument matched by ISIN, but ticker metadata did not match",
                }
            )
        return isin_match, warnings, errors

    if provider_match is not None:
        if ticker_match is not None and ticker_match != provider_match:
            errors.append(
                {
                    "code": "instrument_reference_conflict",
                    "message": "Provider code and exchange+ticker match different instruments",
                }
            )
            return None, warnings, errors
        if isinstance(isin, str):
            warnings.append(
                {
                    "code": "instrument_isin_not_found",
                    "message": "ISIN was not found; instrument matched by provider code",
                }
            )
        return provider_match, warnings, errors

    if ticker_match is not None:
        if isinstance(isin, str):
            warnings.append(
                {
                    "code": "instrument_isin_not_found",
                    "message": "ISIN was not found; instrument matched by exchange+ticker+currency",
                }
            )
        return ticker_match, warnings, errors

    warnings.append(
        {
            "code": "instrument_match_required",
            "message": "No canonical instrument matched the supplied identifiers",
        }
    )
    return None, warnings, errors


def _validate_candidate(
    candidate: dict[str, object],
    *,
    portfolio_id: UUID,
    account_id: UUID,
) -> tuple[dict[str, object] | None, list[Diagnostic]]:
    candidate_copy = dict(candidate)
    source_operation_id = candidate_copy.pop("source_operation_id", None)
    try:
        operation = _OPERATION_ADAPTER.validate_python(
            {
                **candidate_copy,
                "portfolio_id": portfolio_id,
                "account_id": account_id,
            }
        )
    except ValidationError as exc:
        diagnostics: list[Diagnostic] = []
        for error in exc.errors(include_input=False, include_url=False):
            diagnostics.append(
                {
                    "code": str(error["type"]),
                    "message": str(error["msg"]),
                    "location": [str(item) for item in error["loc"]],
                }
            )
        return None, diagnostics

    serialized = operation.model_dump(
        mode="json",
        exclude={"portfolio_id", "account_id", "correction_of_operation_id"},
        exclude_none=True,
    )
    if source_operation_id is not None:
        serialized["source_operation_id"] = source_operation_id
    return serialized, []
