"""Remote Prometheus queries projected onto the existing perf metric contract."""

from harness_toolbox.errors import ErrorKind, PrometheusQueryError
from harness_toolbox.prometheus_query import PrometheusQueryDataSource, PrometheusQueryOptions

from perf_harness.observe.base import ProbeContext
from perf_harness.observe.prometheus import PrometheusQuery, _PrometheusResultProbe


class PrometheusQueryProbe(_PrometheusResultProbe):
    """Query a shared Prometheus server; caller-owned PromQL selects the workload.

    Remote history is not arm-local. Select the intended environment/tenant and
    use a rate window appropriate to the experiment; service labels are attribution,
    not automatic PromQL filters.
    """

    name = "prometheus_query"
    source = "prometheus_query"

    def __init__(
        self,
        *,
        url: str,
        queries: list[PrometheusQuery],
        service: str | None = None,
        headers: dict[str, str] | None = None,
        timeout_ms: int = 5_000,
        max_response_bytes: int = 16 * 1024 * 1024,
        max_series: int = 20_000,
        connection_pool_maxsize: int = 8,
    ) -> None:
        super().__init__(queries=queries, service=service)
        # Explicit remote credentials never inherit the load target's headers.
        self.data_source = PrometheusQueryDataSource(
            url=url,
            headers={**(headers or {}), "User-Agent": "quality-harness/perf"},
            options=PrometheusQueryOptions(
                timeout_ms=timeout_ms,
                max_response_bytes=max_response_bytes,
                max_series=max_series,
                connection_pool_maxsize=connection_pool_maxsize,
            ),
        )

    async def sample(self, ctx: ProbeContext) -> dict[str, float]:
        client = await ctx.clients.get(self.data_source)
        results = await client.read(
            [query.promql for query in self.queries],
            timestamp=ctx.sample_time_s,
            scope=ctx.reads,
        )
        if any(result.warnings for result in results.values()):
            # Partial server results must not masquerade as a complete measurement.
            raise PrometheusQueryError(
                "Prometheus query returned warnings; observation may be incomplete",
                kind=ErrorKind.OPERATION_FAILED,
            )
        return self._project({expression: result.data for expression, result in results.items()})
