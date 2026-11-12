"""PromQL query descriptors and numeric/label projection shared by metric probes."""

import math
from dataclasses import dataclass, field

from harness_common import Operation

from perf_harness.metric import MetricValueKind, series_id
from perf_harness.model import WindowSelector
from perf_harness.observe.base import FamilySpec, Probe


@dataclass(frozen=True)
class PrometheusQuery:
    """One bounded PromQL result exported into perf's resource metric table.

    ``labels`` is the declared output contract and cardinality boundary. Prometheus may
    evaluate PromQL, but every returned vector must carry exactly
    these labels after target-owned labels are removed.
    """

    name: str
    promql: str
    value_kind: MetricValueKind = "gauge"
    unit: str = ""
    description: str = ""
    labels: tuple[str, ...] = ()


@dataclass(frozen=True)
class WindowQuery:
    """Instant query at a window end; $window expands to its millisecond duration."""

    name: str
    promql: str
    unit: str = ""
    description: str = ""
    labels: tuple[str, ...] = ()
    window: WindowSelector = field(default_factory=lambda: WindowSelector(kind="observation"))


class _PrometheusResultProbe(Probe):
    """Shared metric/label projection; subclasses own only their query source."""

    def __init__(
        self,
        *,
        queries: list[PrometheusQuery],
        service: str | None = None,
        operation: Operation | None = None,
    ) -> None:
        self.queries = list(queries)
        self._service = service
        self.operation = operation
        if operation is not None and operation.service_name != service:
            raise ValueError("metric operation owner must match the observed service")
        self.families = {
            query.name: FamilySpec(
                query.unit,
                query.value_kind,
                query.description,
                query.labels,
            )
            for query in self.queries
        }
        if len(self.families) != len(self.queries):
            raise ValueError("Prometheus query names must be unique within one probe")
        if service:
            self.name = f"{self.name}.{service}"
        if operation is not None:
            self.name += f".{operation.id()}"
        for query in queries:
            if set(query.labels) & set(self.labels):
                raise ValueError("query labels cannot override the observation binding")

    @property
    def labels(self) -> dict[str, str]:
        """API observations remain independent of topology and generated traffic.

        @spec Operation ID is model-owned; service binding cannot be overridden by a vector.
        """
        labels = super().labels
        if self.operation is not None:
            labels["operation"] = self.operation.id()
        return labels

    def _project(
        self, results: dict[str, dict], queries: list[PrometheusQuery] | None = None
    ) -> dict[str, float]:
        out = {}
        for query in self.queries if queries is None else queries:
            out.update(project_result(query, results[query.promql]))
        return out


def project_result(query: PrometheusQuery, data: dict) -> dict[str, float]:
    """Project a numeric instant result without losing or collapsing declared labels."""
    out = {}
    if data["resultType"] == "scalar":
        if query.labels:
            raise ValueError("Prometheus scalar query cannot declare output labels")
        out[query.name] = finite_number(data["result"][1])
        return out
    if data["resultType"] != "vector":
        raise ValueError("Prometheus instant query must return scalar or vector")
    for row in data["result"]:
        labels = {key: value for key, value in row["metric"].items() if key != "__name__"}
        if set(labels) != set(query.labels):
            raise ValueError(
                f"Prometheus query {query.name!r} returned undeclared or missing labels"
            )
        key = series_id(query.name, labels)
        if key in out:
            raise ValueError(f"Prometheus query {query.name!r} returned duplicate series")
        out[key] = finite_number(row["value"][1])
    return out


def finite_number(value: object) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError) as exc:
        raise ValueError("Prometheus query returned an invalid numeric sample") from exc
    if not math.isfinite(number):
        raise ValueError("Prometheus query returned a non-finite sample")
    return number
