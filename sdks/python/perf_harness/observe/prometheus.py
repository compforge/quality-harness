"""PromQL query descriptors and numeric/label projection shared by metric probes."""

import math
from dataclasses import dataclass

from perf_harness.metric import MetricValueKind, series_id
from perf_harness.observe.base import FamilySpec, Probe


@dataclass(frozen=True)
class PrometheusQuery:
    """One bounded PromQL result exported into perf's resource metric table.

    ``labels`` is the declared output contract and cardinality boundary. Prombed may
    evaluate arbitrary supported PromQL, but every returned vector must carry exactly
    these labels after target-owned labels are removed.
    """

    name: str
    promql: str
    value_kind: MetricValueKind = "gauge"
    unit: str = ""
    description: str = ""
    labels: tuple[str, ...] = ()


class _PrometheusResultProbe(Probe):
    """Shared metric/label projection; subclasses own only their query source."""

    def __init__(self, *, queries: list[PrometheusQuery], service: str | None = None) -> None:
        self.queries = list(queries)
        self._service = service
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

    @staticmethod
    def _labels(metric: dict[str, str]) -> dict[str, str]:
        return {key: value for key, value in metric.items() if key != "__name__"}

    @staticmethod
    def _number(value: object) -> float:
        try:
            number = float(value)
        except (TypeError, ValueError) as exc:
            raise ValueError("Prometheus query returned an invalid numeric sample") from exc
        if not math.isfinite(number):
            raise ValueError("Prometheus query returned a non-finite sample")
        return number

    def _record_vector(
        self,
        out: dict[str, float],
        query: PrometheusQuery,
        rows: list[dict],
    ) -> None:
        expected = set(query.labels)
        for row in rows:
            labels = self._labels(row["metric"])
            if set(labels) != expected:
                raise ValueError(
                    f"Prometheus query {query.name!r} declared labels {sorted(expected)!r} "
                    f"but returned {sorted(labels)!r}"
                )
            key = series_id(query.name, labels)
            if key in out:
                raise ValueError(f"Prometheus query {query.name!r} returned duplicate series {key}")
            out[key] = self._number(row["value"][1])

    def _project(
        self, results: dict[str, dict], queries: list[PrometheusQuery] | None = None
    ) -> dict[str, float]:
        out: dict[str, float] = {}
        for query in self.queries if queries is None else queries:
            data = results[query.promql]
            if data["resultType"] == "scalar":
                if query.labels:
                    raise ValueError(
                        f"Prometheus scalar query {query.name!r} cannot declare output labels"
                    )
                out[query.name] = self._number(data["result"][1])
                continue
            if data["resultType"] != "vector":
                raise ValueError(
                    f"Prometheus query {query.name!r} returned unsupported "
                    f"result type {data['resultType']!r}"
                )
            self._record_vector(out, query, data["result"])
        return out
