"""Direct service metric scraping, trends and local window summaries."""

import math
from dataclasses import dataclass, field, replace
from urllib.parse import urlsplit

from harness_common import KubernetesWorkload
from harness_toolbox.environment import KubernetesEnvironment
from harness_toolbox.prometheus import PrometheusDataSource, PrometheusOptions
from prombed import ScrapeTarget

from perf_harness.model import ProbeWindowObservation, Service, Window, WindowSelector
from perf_harness.observe.base import FamilySpec, ProbeContext
from perf_harness.observe.prometheus import PrometheusQuery, _PrometheusResultProbe


@dataclass(frozen=True)
class WindowQuery:
    """Instant PromQL at a window end; $window expands to its inclusive millisecond range."""

    name: str
    promql: str
    unit: str = ""
    description: str = ""
    labels: tuple[str, ...] = ()
    window: WindowSelector = field(default_factory=lambda: WindowSelector(kind="observation"))


class MetricProbe(_PrometheusResultProbe):
    """Scrape /metrics directly and summarize service-time windows."""

    name = "metric"
    source = "prometheus"

    def __init__(
        self,
        *,
        queries: list[PrometheusQuery] | None = None,
        summaries: list[WindowQuery] | None = None,
        service: str | None = None,
        target_service: Service | None = None,
        path: str = "/metrics",
        url: str | None = None,
        targets: tuple[ScrapeTarget, ...] = (),
        headers: dict[str, str] | None = None,
        timeout_ms: int = 5_000,
        max_scrape_bytes: int = 16 * 1024 * 1024,
        retention_ms: int | None = None,
        max_series: int = 20_000,
        max_samples_per_series: int = 10_000,
        connection_pool_maxsize: int = 8,
    ) -> None:
        super().__init__(queries=queries or [], service=service)
        self.summaries = list(summaries or [])
        self.needs_observation_window = any(q.window.kind == "observation" for q in self.summaries)
        if not self.queries and not self.summaries:
            raise ValueError("MetricProbe requires queries or summaries")
        if not path.startswith("/") or urlsplit(path).netloc:
            raise ValueError("Metrics path must be an absolute URL path")
        if url and targets:
            raise ValueError("Configure a metrics URL or targets, not both")
        for summary in self.summaries:
            if summary.name in self.families or summary.name == "up":
                raise ValueError("Metric query names must be unique and cannot use up")
            self.families[summary.name] = FamilySpec(
                summary.unit, "scalar", summary.description, summary.labels
            )
        self._target_service = target_service
        self._url, self._path, self._targets = url, path, targets
        self._retention_ms = retention_ms
        self._headers = dict(headers or {})
        self._options = PrometheusOptions(
            timeout_ms,
            max_scrape_bytes,
            10 * 60_000 if retention_ms is None else retention_ms,
            max_series,
            max_samples_per_series,
            connection_pool_maxsize,
        )

    def _source(self, ctx: ProbeContext) -> PrometheusDataSource:
        service = self._target_service or ctx.service
        implicit = not self._url and not self._targets
        options = self._options
        if self._retention_ms is None and ctx.observation_budget_s is not None:
            # Keep the declared execution window, with room for boundary scrapes.
            options = replace(
                options,
                retention_ms=max(
                    options.retention_ms,
                    math.ceil(ctx.observation_budget_s * 1000) + 2 * options.timeout_ms,
                ),
            )
        headers = {**(service.headers if implicit else {}), **self._headers}
        headers["User-Agent"] = "quality-harness/perf"
        if implicit and service.workloads:
            from harness_toolbox.prometheus_discovery import KubernetesScrapeDiscovery

            if not isinstance(service.environment, KubernetesEnvironment) or any(
                not isinstance(workload, KubernetesWorkload) for workload in service.workloads
            ):
                raise ValueError(
                    "Metric discovery requires Kubernetes workloads or explicit targets"
                )
            address = urlsplit(service.base_url)
            port = service.metrics_port
            return PrometheusDataSource(
                headers=headers,
                options=options,
                discovery=KubernetesScrapeDiscovery(
                    service.environment,
                    tuple(service.workloads),
                    port,
                    self._path,
                    address.scheme or "http",
                ),
            )
        url = self._url or (service.base_url.rstrip("/") + self._path if not self._targets else "")
        return PrometheusDataSource(url, headers=headers, options=options, targets=self._targets)

    @staticmethod
    def _labels(metric: dict[str, str]) -> dict[str, str]:
        # Preserve physical instance identity; callers aggregate explicitly in PromQL.
        return {key: value for key, value in metric.items() if key != "__name__"}

    async def sample(self, ctx: ProbeContext) -> dict[str, float]:
        client = await ctx.clients.get(self._source(ctx))
        results = await client.read([query.promql for query in self.queries], scope=ctx.reads)
        return self._project(results)

    async def finish(
        self, ctx: ProbeContext, windows: list[Window]
    ) -> list[ProbeWindowObservation]:
        source = self._source(ctx)
        observations = []
        for query in self.summaries:
            for window in windows:
                if not query.window.matches(window):
                    continue
                start = math.ceil((ctx.wall_origin_s + ctx.t0 + window.start_s) * 1000)
                end = math.ceil((ctx.wall_origin_s + ctx.t0 + window.end_s) * 1000) - 1
                expression = query.promql
                values: dict[str, float] = {}
                error = None
                try:
                    client = await ctx.clients.get(source)
                    # Full observation spans the actual baseline and final scrape,
                    # including all completions during request cooldown.
                    if window.kind == "observation":
                        start, end = client.history_bounds
                    duration = end - start + 1
                    expression = query.promql.replace("$window", f"{duration}ms")
                    results = client.query_window([expression], start_ms=start, end_ms=end)
                    values = self._project(
                        results, [PrometheusQuery(query.name, expression, labels=query.labels)]
                    )
                except Exception as exc:
                    error = str(exc)
                observations.append(
                    ProbeWindowObservation(
                        self.name,
                        window.id,
                        query.name,
                        expression,
                        source.client_key,
                        start,
                        end,
                        values,
                        error,
                    )
                )
        return observations
