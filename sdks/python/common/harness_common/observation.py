"""Metric addresses resolved for a specific observation target."""

from dataclasses import dataclass


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
