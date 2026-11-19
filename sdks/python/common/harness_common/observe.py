"""Reusable measurements. The containing Service, Operation or Workload supplies the target."""

from dataclasses import dataclass, field
from typing import Mapping


@dataclass(frozen=True)
class HistogramQuantile:
    """Explicit classic-histogram projection; source expression returns bucket increments."""

    quantile: float = 0.95
    scale: float = 1.0

    def __post_init__(self) -> None:
        import math

        if (
            not 0 < self.quantile < 1
            or not math.isfinite(self.scale)
            or self.scale <= 0
        ):
            raise ValueError(
                "histogram requires 0 < quantile < 1 and a positive finite scale"
            )


@dataclass(frozen=True)
class MetricQuery:
    expression: str
    unit: str
    description: str = ""
    semantics: str = ""
    histogram: HistogramQuantile | None = None
    category: str | None = field(default=None, kw_only=True)
    trend_window_seconds: int | None = field(default=None, kw_only=True)


@dataclass(frozen=True)
class ObservationDefinition:
    """Capability type and optional concrete backend constraint, without source access.

    @spec An omitted kind applies to all backends; selection never retries another variant.
    """

    type: str
    metrics: Mapping[str, MetricQuery]
    kind: str | None = field(default=None, kw_only=True)
    response_mode: str | None = field(default=None, kw_only=True)


def select_observations(
    observations: tuple[ObservationDefinition, ...], backend: str
) -> tuple[ObservationDefinition, ...]:
    """Every declared role has one matching producer; no failure-triggered fallback."""
    for observation in observations:
        if not observation.type.strip() or not observation.metrics:
            raise ValueError("Observation requires a type and metric roles")
        if observation.kind is not None and (
            not isinstance(observation.kind, str) or not observation.kind.strip()
        ):
            raise ValueError("Observation kind must be a nonempty string")
        for role, metric in observation.metrics.items():
            if (
                not role.strip()
                or not metric.unit.strip()
                or not metric.expression.strip()
            ):
                raise ValueError("Metric requires role, unit and expression")
    selected = tuple(p for p in observations if p.kind is None or p.kind == backend)
    for role in {role for p in observations for role in p.metrics}:
        if sum(role in p.metrics for p in selected) != 1:
            raise ValueError(
                f"Metric {role!r} requires exactly one producer for backend {backend!r}"
            )
    return selected
