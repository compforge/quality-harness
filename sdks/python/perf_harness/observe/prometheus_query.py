"""Describe post-load Prometheus queries; collection and projection live in history."""

import math

from harness_common import Operation
from harness_toolbox.prometheus import PrometheusDataSource

from perf_harness.model import ArmRun, PrometheusObservation
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
    ) -> None:
        super().__init__(queries=queries or [], service=service, operation=operation)
        if not math.isfinite(step_s) or step_s <= 0:
            raise ValueError("Prometheus query step_s must be finite and positive")
        self.data_source = data_source
        self.step_s = step_s
        self.summaries = list(summaries or [])
        if not self.queries and not self.summaries:
            raise ValueError("prometheus_query requires queries or summaries")
        for summary in self.summaries:
            if set(summary.labels) & set(self.labels):
                raise ValueError("summary labels cannot override the observation binding")
            if summary.name in self.families or summary.name == "up":
                raise ValueError("Prometheus query names must be unique and cannot use up")
            self.families[summary.name] = FamilySpec(
                summary.unit, "scalar", summary.description, summary.labels
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
