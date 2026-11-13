"""Describe post-load Prometheus queries; collection and projection live in history."""

import math

from harness_common import HttpOperation, Operation
from harness_toolbox.prometheus import PrometheusDataSource

from perf_harness.metric import series_id
from perf_harness.model import ApiMetricBinding, ArmRun, PrometheusObservation
from perf_harness.observe.base import FamilySpec, ProbeContext
from perf_harness.observe.prometheus import PrometheusQuery, WindowQuery, _PrometheusResultProbe


class PrometheusQueryProbe(_PrometheusResultProbe):
    """Query existing history after load; service binding never rewrites PromQL.

    @why /metrics responses can be large; repeated full scrapes add transfer and parsing
         cost and can compromise observation timeliness and completeness. Perf reuses
         external Prometheus history instead of running a second scraper.
    @link component://sdks/python/perf_harness/docs/service-metrics.md
    """

    name = "prometheus_query"
    source = "prometheus_query"
    periodic = False
    needs_observation_window = True

    def __init__(
        self,
        *,
        data_source: PrometheusDataSource,
        queries: list[PrometheusQuery] | None = None,
        summaries: list[WindowQuery] | None = None,
        service: str | None = None,
        operation: Operation | None = None,
        step_s: float = 15,
        api_metrics: dict[str, str] | None = None,
        qps_semantics: str = "completed",
    ) -> None:
        super().__init__(queries=queries or [], service=service, operation=operation)
        if not math.isfinite(step_s) or step_s <= 0:
            raise ValueError("Prometheus query step_s must be finite and positive")
        self.data_source = data_source
        self.step_s = step_s
        self.summaries = list(summaries or [])
        if not self.queries and not self.summaries:
            raise ValueError("prometheus_query requires queries or summaries")
        for index, summary in enumerate(self.summaries):
            if set(summary.labels) & set(self.labels):
                raise ValueError("summary labels cannot override the observation binding")
            if summary.name in {q.name for q in self.queries} or summary.name == "up":
                raise ValueError("Prometheus query names must be unique and cannot use up")
            previous = [q for q in self.summaries[:index] if q.name == summary.name]
            if any(
                q.window.kind == summary.window.kind
                or (q.unit, q.labels) != (summary.unit, summary.labels)
                for q in previous
            ):
                raise ValueError("duplicate or inconsistent Prometheus window summary")
            self.families[summary.name] = FamilySpec(
                summary.unit, "scalar", summary.description, summary.labels
            )

        self.api_metrics = None
        if api_metrics is not None:
            if (
                operation is None
                or not isinstance(api_metrics, dict)
                or set(api_metrics) not in ({"qps"}, {"qps", "p95"})
            ):
                raise ValueError(
                    "api_metrics requires operation and a qps summary; p95 is optional"
                )
            if qps_semantics not in {"entered", "completed"}:
                raise ValueError("qps_semantics must be entered or completed")
            for role, name in api_metrics.items():
                summaries = [q for q in self.summaries if q.name == name]
                if not summaries or any(
                    q.labels or q.unit != {"qps": "rps", "p95": "s"}[role] for q in summaries
                ):
                    raise ValueError(
                        "api_metrics must reference unlabeled window summaries in rps/seconds"
                    )
            self.api_metrics = ApiMetricBinding(
                operation.id(),
                operation.service_name,
                series_id(f"{self.family}.{api_metrics['qps']}", self.labels) + ".value",
                (series_id(f"{self.family}.{api_metrics['p95']}", self.labels) + ".value")
                if "p95" in api_metrics
                else None,
                operation.method if isinstance(operation, HttpOperation) else "",
                operation.path if isinstance(operation, HttpOperation) else "",
                qps_semantics,
            )

    async def sample(self, ctx: ProbeContext) -> dict[str, float]:
        raise RuntimeError("Prometheus observations are collected after load")

    def plan(self, arm: ArmRun) -> list[PrometheusObservation]:
        """Freeze query windows before network I/O so failed reads remain replayable."""
        if arm.started_at_s is None:
            return []
        observations = []
        for window in arm.windows:
            start = math.ceil((arm.started_at_s + window.start_s) * 1000) / 1000
            end = (math.ceil((arm.started_at_s + window.end_s) * 1000) - 1) / 1000
            if end < start:
                continue
            if window.kind == "observation":
                for query in self.queries:
                    observations.append(
                        PrometheusObservation(
                            self.name,
                            f"{self.family}.{query.name}",
                            query.promql,
                            self.data_source.url,
                            start,
                            end,
                            self.step_s,
                            self.labels,
                            query.labels,
                        )
                    )
            for query in self.summaries:
                if query.window.matches(window):
                    duration_ms = round((end - start) * 1000) + 1
                    observations.append(
                        PrometheusObservation(
                            self.name,
                            f"{self.family}.{query.name}",
                            query.promql.replace("$window", f"{duration_ms}ms"),
                            self.data_source.url,
                            start,
                            end,
                            None,
                            self.labels,
                            query.labels,
                            window.id,
                        )
                    )
        return observations
