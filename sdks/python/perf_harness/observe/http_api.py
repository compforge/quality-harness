"""HTTP server metric matching by service, method and route template."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from typing import Literal

from harness_common import HttpOperation
from harness_toolbox.prometheus import PrometheusDataSource

from perf_harness.model import HistogramQuantile, WindowSelector
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
    status_label: str | None = None
    error_status_pattern: str | None = None

    def __post_init__(self) -> None:
        for name in (self.requests_metric, self.duration_bucket):
            if not isinstance(name, str) or not re.fullmatch(r"[a-zA-Z_:][a-zA-Z0-9_:]*", name):
                raise ValueError("HTTP metric names must be Prometheus identifiers")
        labels = (self.service_label, self.method_label, self.route_label)
        if (self.status_label is None) != (self.error_status_pattern is None):
            raise ValueError("HTTP errors require status_label and error_status_pattern together")
        if self.status_label is not None:
            if not re.fullmatch(r"[a-zA-Z_][a-zA-Z0-9_]*", self.status_label):
                raise ValueError("HTTP status_label must be a Prometheus identifier")
            if self.status_label in labels or self.status_label in self.match:
                raise ValueError("HTTP status label cannot override request selectors")
            if not isinstance(self.error_status_pattern, str) or not self.error_status_pattern:
                raise ValueError("HTTP error_status_pattern must be nonempty")
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
            result = {
                "qps": f"sum(rate({count}[{interval}]))",
                "p95": f"histogram_quantile(0.95, sum by (le) (rate({buckets}[{interval}])))"
                + scale,
            }
            if profile.status_label is not None:
                # An absent status schema is missing, not zero errors. Restrict both counts
                # to series carrying status, and only zero-fill a missing error series.
                total_selector = selector[:-1] + f',{profile.status_label}!=""' + "}"
                error_selector = (
                    total_selector[:-1]
                    + f",{profile.status_label}=~{json.dumps(profile.error_status_pattern)}"
                    + "}"
                )
                total = f"sum(increase({profile.requests_metric}{total_selector}[{interval}]))"
                errors = f"sum(increase({profile.requests_metric}{error_selector}[{interval}]))"
                result["error_rate"] = f"(({errors}) or (0 * ({total}))) / (({total}) > 0)"
            return result

        summaries = [
            WindowQuery(
                role,
                f"sum by (le) (increase({buckets}[$window]))" if role == "p95" else promql,
                histogram=HistogramQuantile(scale=0.001 if profile.duration_unit == "ms" else 1)
                if role == "p95"
                else None,
                unit={"qps": "rps", "p95": "s", "error_rate": "ratio"}[role],
                description=(
                    f"HTTP request counter ({profile.qps_semantics}); matching service traffic, "
                    "not isolated run traffic or SSE business completion"
                    if role == "qps"
                    else "HTTP status failures / completed HTTP requests; "
                    "excludes SSE business errors"
                    if role == "error_rate"
                    else "HTTP duration histogram p95; matching service traffic; instrumentation "
                    "boundary may differ from complete SSE duration"
                ),
                window=WindowSelector(kind=kind),
            )
            for kind in ("full", "phase", "custom")
            for role, promql in expressions("$window").items()
        ]
        queries = [
            PrometheusQuery(
                role + "_trend",
                promql,
                unit={"qps": "rps", "p95": "s", "error_rate": "ratio"}[role],
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
            metrics={role: role for role in expressions("$window")},
            qps_semantics=profile.qps_semantics,
        )
