"""Describe post-load Prometheus queries; collection and projection live in history."""

import math

from harness_common import HttpOperation, Operation
from harness_toolbox.prometheus import PrometheusDataSource

from perf_harness.metric import series_id
from perf_harness.model import (
    ArmRun,
    MetricBinding,
    ObservationBinding,
    PrometheusObservation,
    Service,
)
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
        metrics: dict[str, str] | None = None,
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
                q.window == summary.window or (q.unit, q.labels) != (summary.unit, summary.labels)
                for q in previous
            ):
                raise ValueError("duplicate or inconsistent Prometheus window summary")
            self.families[summary.name] = FamilySpec(
                summary.unit, "scalar", summary.description, summary.labels
            )

        if qps_semantics not in {"entered", "completed"}:
            raise ValueError("qps_semantics must be entered or completed")
        self.observation_binding = None
        if metrics is not None:
            if not service or not metrics:
                raise ValueError("metrics requires a service and metric mappings")
            contributions = {}
            for role, name in metrics.items():
                selected = [q for q in self.summaries if q.name == name]
                if not selected or any(q.labels for q in selected):
                    raise ValueError("operation metrics must reference unlabeled window summaries")
                if len({q.description for q in selected}) != 1:
                    raise ValueError("operation metric descriptions must agree across windows")
                query = selected[0]
                contributions[role] = MetricBinding(
                    series_id(f"{self.family}.{name}", self.labels) + ".value",
                    query.unit,
                    query.description,
                    qps_semantics if role == "qps" else "",
                )
            self.observation_binding = ObservationBinding(
                target=operation.id() if operation else Service(service).id(),
                service=service,
                metrics=contributions,
                method=operation.method if isinstance(operation, HttpOperation) else "",
                path=operation.path if isinstance(operation, HttpOperation) else "",
                response_mode=operation.response_mode
                if isinstance(operation, HttpOperation)
                else None,
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
            if window.kind == "full":
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
                            datasource_kind=self.data_source.kind,
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
                            histogram=query.histogram,
                            datasource_kind=self.data_source.kind,
                        )
                    )
        return observations
