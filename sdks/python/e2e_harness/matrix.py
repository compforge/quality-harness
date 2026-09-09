"""Deterministic variant-matrix expansion for e2e case arms."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field


@dataclass(frozen=True)
class Variant:
    values: Mapping[str, str] = field(default_factory=dict)

    @property
    def id(self) -> str | None:
        if not self.values:
            return None
        return ",".join(f"{key}={self.values[key]}" for key in sorted(self.values))


def expand_matrix(axes: Mapping[str, Sequence[str]]) -> list[Variant]:
    """Expand a Cartesian product with stable axis and value order."""
    if not axes:
        return [Variant()]
    variants: list[dict[str, str]] = [{}]
    for axis in sorted(axes):
        values = axes[axis]
        if not axis or not values or any(not value for value in values):
            raise ValueError("matrix axes and values must be non-empty")
        variants = [
            {**variant, axis: value} for variant in variants for value in values
        ]
    return [Variant(variant) for variant in variants]
