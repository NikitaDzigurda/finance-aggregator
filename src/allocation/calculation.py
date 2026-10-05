from __future__ import annotations

from collections import defaultdict
from collections.abc import Sequence
from dataclasses import dataclass
from decimal import ROUND_HALF_EVEN, Context, Decimal, localcontext
from uuid import UUID

from analytics.contracts import DataQualityState
from shared.exact import RATE_SPEC, validate_decimal

_ALLOCATION_CONTEXT = Context(prec=100, rounding=ROUND_HALF_EVEN)
_RATE_QUANTUM = Decimal("0.000000000000000000000001")


@dataclass(frozen=True, slots=True)
class AllocationValuationComponent:
    category_id: UUID
    value: Decimal | None


@dataclass(frozen=True, slots=True)
class ActualAllocationWeight:
    category_id: UUID
    known_value: Decimal
    weight: Decimal


@dataclass(frozen=True, slots=True)
class ActualAllocation:
    quality: DataQualityState
    denominator: Decimal
    included_components: int
    excluded_components: int
    weights: tuple[ActualAllocationWeight, ...]
    diagnostic_codes: tuple[str, ...]


def calculate_actual_allocation(
    components: Sequence[AllocationValuationComponent],
) -> ActualAllocation:
    """Calculate exact known-value weights without concealing omitted components."""
    grouped: defaultdict[UUID, Decimal] = defaultdict(Decimal)
    included = 0
    excluded = 0
    for component in components:
        if component.value is None:
            excluded += 1
            continue
        grouped[component.category_id] += component.value
        included += 1

    denominator = sum(grouped.values(), start=Decimal(0))
    diagnostics: list[str] = []
    if excluded:
        diagnostics.append("allocation_component_unavailable")
    if any(value < 0 for value in grouped.values()):
        diagnostics.append("allocation_negative_component")
    if included == 0 or denominator <= 0:
        diagnostics.append("allocation_denominator_nonpositive")
        return ActualAllocation(
            quality=DataQualityState.UNAVAILABLE,
            denominator=denominator,
            included_components=included,
            excluded_components=excluded,
            weights=(),
            diagnostic_codes=tuple(diagnostics),
        )

    with localcontext(_ALLOCATION_CONTEXT):
        rounded = {
            category_id: validate_decimal(
                (value / denominator).quantize(
                    _RATE_QUANTUM,
                    rounding=ROUND_HALF_EVEN,
                ),
                RATE_SPEC,
            )
            for category_id, value in grouped.items()
        }
        residual = Decimal(1) - sum(rounded.values(), start=Decimal(0))
        if residual:
            residual_category = min(
                grouped,
                key=lambda category_id: (-grouped[category_id], str(category_id)),
            )
            rounded[residual_category] = validate_decimal(
                rounded[residual_category] + residual,
                RATE_SPEC,
            )

    quality = DataQualityState.PARTIAL if excluded else DataQualityState.COMPLETE
    return ActualAllocation(
        quality=quality,
        denominator=denominator,
        included_components=included,
        excluded_components=excluded,
        weights=tuple(
            ActualAllocationWeight(
                category_id=category_id,
                known_value=grouped[category_id],
                weight=rounded[category_id],
            )
            for category_id in sorted(grouped, key=str)
        ),
        diagnostic_codes=tuple(diagnostics),
    )
