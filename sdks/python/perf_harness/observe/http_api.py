"""HTTP server metric matching by service, method and route template."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from typing import Literal

from harness_common import HttpOperation
from harness_toolbox.prometheus import PrometheusDataSource

from perf_harness.model import WindowSelector
from perf_harness.observe.prometheus import PrometheusQuery, WindowQuery
from perf_harness.observe.prometheus_query import PrometheusQueryProbe


@dataclass(frozen=True)
class HttpMetricProfile:
    """Service-owned Prometheus schema. Matchers scope metrics to the intended deployment.

    @spec Operation identity is unchanged by label names or exported route spelling.
    @rule Use server metrics only; client/server observations must not be added together.
    """

    requests_metric: str
    duration_bucket: str
    service_label: str = "service_name"
    method_label: str = "http_request_method"
    route_label: str = "http_route"
    service_value: str | None = None
    match: dict[str, str] = field(default_factory=dict)
    duration_unit: Literal["s", "ms"] = "s"
    qps_semantics: Literal["entered", "completed"] = "completed"
    rate_interval: str = "5m"

    def __post_init__(self) -> None:
        for name in (self.requests_metric, self.duration_bucket):
            if not isinstance(name, str) or not re.fullmatch(r"[a-zA-Z_:][a-zA-Z0-9_:]*", name):
                raise ValueError("HTTP metric names must be Prometheus identifiers")
        labels = (self.service_label, self.method_label, self.route_label)
        if len(set(labels)) != 3:
            raise ValueError("HTTP service/method/route labels must be distinct")
        if not isinstance(self.match, dict):
            raise ValueError("HTTP metric match must be a label mapping")
        for name in (*labels, *self.match):
            if not isinstance(name, str) or not re.fullmatch(r"[a-zA-Z_][a-zA-Z0-9_]*", name):
                raise ValueError("HTTP metric labels must be Prometheus identifiers")
        if set(self.match) & set(labels):
            raise ValueError("HTTP metric match cannot override service/method/route labels")
        if any(not isinstance(value, str) for value in self.match.values()):
            raise ValueError("HTTP metric matcher values must be strings")
        if self.service_value is not None and (
            not isinstance(self.service_value, str) or not self.service_value
        ):
            raise ValueError("HTTP metric service_value must be nonempty")
        if self.duration_unit not in {"s", "ms"} or self.qps_semantics not in {
            "entered",
            "completed",
        }:
            raise ValueError(
                "HTTP metrics require duration_unit s/ms and qps_semantics entered/completed"
            )
        if not isinstance(self.rate_interval, str) or not re.fullmatch(
            r"[1-9][0-9]*(ms|s|m|h)", self.rate_interval
        ):
            raise ValueError("HTTP metric rate_interval must be a positive Prometheus duration")

    def selector(self, operation: HttpOperation, metric_route: str | None = None) -> str:
        if (
            not isinstance(operation.method, str)
            or not operation.method
            or not isinstance(operation.path, str)
            or not operation.path.startswith("/")
        ):
            raise ValueError("HTTP operation requires method and an absolute route template")
        if metric_route is not None and (not isinstance(metric_route, str) or not metric_route):
            raise ValueError("metric_route must be a nonempty route template")
        labels = self.match | {
            self.service_label: self.service_value or operation.service_name,
            self.method_label: operation.method,
            self.route_label: metric_route or operation.path,
        }
        return (
            "{"
            + ",".join(
                f"{key}={json.dumps(value, ensure_ascii=False)}"
                for key, value in sorted(labels.items())
            )
            + "}"
        )


class HttpApiProbe(PrometheusQueryProbe):
    """Generate bounded HTTP queries; use the existing history collector and metric store.

    @spec p95 merges histogram buckets across replicas before calculating the quantile.
    @rule Empty or invalid results remain missing; they never become zero traffic.
    @link component://sdks/python/perf_harness/docs/callgraph.md
    """

    name = "http_api"

    def __init__(
        self,
        *,
        operation: HttpOperation,
        profile: HttpMetricProfile,
        data_source: PrometheusDataSource,
        step_s: float = 15,
        metric_route: str | None = None,
    ) -> None:
        selector = profile.selector(operation, metric_route)
        count = profile.requests_metric + selector
        buckets = profile.duration_bucket + selector
        scale = " / 1000" if profile.duration_unit == "ms" else ""

        def expressions(interval: str) -> dict[str, str]:
            return {
                "qps": f"sum(rate({count}[{interval}]))",
                "p95": f"histogram_quantile(0.95, sum by (le) (rate({buckets}[{interval}])))"
                + scale,
            }

        summaries = [
            WindowQuery(
                role,
                promql,
                unit={"qps": "rps", "p95": "s"}[role],
                description=f"HTTP server {role}; see operation binding for counter semantics",
                window=WindowSelector(kind=kind),
            )
            for kind in ("measurement", "observation", "hold", "ramp", "warmup", "cooldown")
            for role, promql in expressions("$window").items()
        ]
        queries = [
            PrometheusQuery(
                role + "_trend",
                promql,
                unit={"qps": "rps", "p95": "s"}[role],
                description=f"HTTP server {role}; rolling query interval",
            )
            for role, promql in expressions(profile.rate_interval).items()
        ]
        super().__init__(
            data_source=data_source,
            service=operation.service_name,
            operation=operation,
            queries=queries,
            summaries=summaries,
            step_s=step_s,
            api_metrics={"qps": "qps", "p95": "p95"},
            qps_semantics=profile.qps_semantics,
        )
