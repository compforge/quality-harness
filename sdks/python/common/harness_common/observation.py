"""Offline query observation intent; domains own phases, windows and recorded facts."""

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
class MetricBinding:
    """An observed attribute points into MetricStore; never a second value store."""

    metric: str
    unit: str
    description: str = ""
    semantics: str = ""

    def __post_init__(self) -> None:
        if not self.metric or not self.unit:
            raise ValueError("metric binding requires address and unit")


@dataclass(frozen=True)
class ObservationBinding:
    """Resolved observation contract, independent of the collecting Probe type.

    @spec Common owns Service/Operation identity; workload names are Service-scoped.
    @rule Metric values belong to Run/Arm/Window, never to the static target.
    """

    target: str
    service: str
    metrics: dict[str, MetricBinding]
    workload: str | None = None
    method: str = ""
    path: str = ""
    response_mode: str | None = None

    def __post_init__(self) -> None:
        if not self.target or not self.service or not self.metrics:
            raise ValueError("observation requires target, service and metrics")
        for name, binding in self.metrics.items():
            if not isinstance(name, str) or not name:
                raise ValueError("observed metric names must be nonempty strings")


@dataclass(frozen=True)
class MetricQuery:
    expression: str
    unit: str
    description: str = ""
    semantics: str = ""
    histogram: HistogramQuantile | None = None


@dataclass(frozen=True)
class ProbeDefinition:
    """Capability type and optional concrete backend constraint, without source access.

    @spec An omitted kind applies to all backends; selection never retries another variant.
    """

    type: str
    metrics: Mapping[str, MetricQuery]
    kind: str | None = field(default=None, kw_only=True)
    response_mode: str | None = field(default=None, kw_only=True)


def select_probes(
    probes: tuple[ProbeDefinition, ...], backend: str
) -> tuple[ProbeDefinition, ...]:
    """Every declared role has one matching producer; no failure-triggered fallback."""
    for probe in probes:
        if not probe.type.strip() or not probe.metrics:
            raise ValueError("Probe requires a type and metric roles")
        if probe.kind is not None and (
            not isinstance(probe.kind, str) or not probe.kind.strip()
        ):
            raise ValueError("Probe kind must be a nonempty string")
        for role, metric in probe.metrics.items():
            if (
                not role.strip()
                or not metric.unit.strip()
                or not metric.expression.strip()
            ):
                raise ValueError("Metric requires role, unit and expression")
    selected = tuple(p for p in probes if p.kind is None or p.kind == backend)
    for role in {role for p in probes for role in p.metrics}:
        if sum(role in p.metrics for p in selected) != 1:
            raise ValueError(
                f"Metric {role!r} requires exactly one producer for backend {backend!r}"
            )
    return selected
