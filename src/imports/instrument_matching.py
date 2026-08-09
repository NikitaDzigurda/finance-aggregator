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
    references = [_reference_from_row(row) for row in rows]
    isins = {
        value
        for reference in references
        if reference is not None
        if isinstance((value := reference.get("isin")), str)
    }
    tickers = {
        value
        for reference in references
        if reference is not None
        if isinstance((value := reference.get("ticker")), str)
    }
    isin_matches = await _load_isin_matches(session, isins)
    ticker_matches = await _load_ticker_matches(session, tickers)

    resolved: list[ParsedRow] = []
    for row, reference in zip(rows, references, strict=True):
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
        if reference is not None:
            instrument_id, match_warnings, match_errors = _match_reference(
                reference,
                isin_matches,
                ticker_matches,
            )
            warnings.extend(match_warnings)
            errors.extend(match_errors)
            if instrument_id is None:
                resolved.append(
                    replace(
                        row,
                        normalized_candidate=candidate_copy,
                        warnings=tuple(warnings),
                        errors=tuple(errors),
                    )
                )
                continue
            payload_copy.pop("instrument_reference", None)
            payload_copy["instrument_id"] = str(instrument_id)

        candidate_copy["payload"] = payload_copy
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


def _reference_from_row(row: ParsedRow) -> dict[str, object] | None:
    candidate = row.normalized_candidate
    if candidate is None:
        return None
    payload = candidate.get("payload")
    if not isinstance(payload, dict):
        return None
    reference = payload.get("instrument_reference")
    return reference if isinstance(reference, dict) else None


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


def _match_reference(
    reference: dict[str, object],
    isin_matches: dict[str, UUID],
    ticker_matches: dict[tuple[str, str, str], UUID],
) -> tuple[UUID | None, list[Diagnostic], list[Diagnostic]]:
    warnings: list[Diagnostic] = []
    errors: list[Diagnostic] = []
    isin = reference.get("isin")
    ticker = reference.get("ticker")
    exchange = reference.get("exchange")
    currency = reference.get("currency")
    isin_match = isin_matches.get(isin) if isinstance(isin, str) else None
    ticker_match = (
        ticker_matches.get((ticker, exchange, currency))
        if isinstance(ticker, str)
        and isinstance(exchange, str)
        and isinstance(currency, str)
        else None
    )

    if isin_match is not None:
        if ticker_match is not None and ticker_match != isin_match:
            errors.append(
                {
                    "code": "instrument_reference_conflict",
                    "message": "ISIN and exchange+ticker+currency match different instruments",
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
