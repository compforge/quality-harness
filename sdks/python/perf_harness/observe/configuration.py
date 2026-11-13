"""Configuration for external Prometheus access, queries and report columns."""

import math
import os
from typing import get_args

from harness_common import HttpOperation, Operation, SSEOperation
from harness_toolbox.prometheus import PrometheusDataSource, PrometheusOptions

from perf_harness.model import ReportColumn, Service, WindowKind, WindowSelector
from perf_harness.observe.prometheus import PrometheusQuery, WindowQuery
from perf_harness.observe.prometheus_query import PrometheusQueryProbe


def parse_window(raw: dict | None, *, default: str) -> WindowSelector:
    raw = raw if raw is not None else {"kind": default}
    if not isinstance(raw, dict) or set(raw) - {"kind", "name", "level"}:
        raise ValueError("window must contain only kind, name and level")
    kind = raw.get("kind", default)
    if kind not in get_args(WindowKind):
        raise ValueError(f"window: invalid kind: {kind!r}")
    if kind not in {"hold", "ramp", "warmup"} and ("name" in raw or "level" in raw):
        raise ValueError("window name/level only apply to load stages")
    return WindowSelector(
        kind, raw.get("name"), float(raw["level"]) if raw.get("level") is not None else None
    )


def parse_prometheus(raw: dict | None) -> tuple[PrometheusDataSource | None, float]:
    """Environment-owned query access. Only environment variable names carry secrets."""
    if raw is None:
        return None, 15
    allowed = {
        "url",
        "headers_env",
        "timeout_ms",
        "max_response_bytes",
        "max_series",
        "connection_pool_maxsize",
        "step_s",
    }
    if not isinstance(raw, dict) or set(raw) - allowed or not raw.get("url"):
        raise ValueError("prometheus requires url and only supported query options")
    headers_env = raw.get("headers_env", {})
    if not isinstance(headers_env, dict) or any(
        not isinstance(k, str) or not isinstance(v, str) or not v for k, v in headers_env.items()
    ):
        raise ValueError(
            "prometheus.headers_env must map header names to environment variable names"
        )
    headers = {}
    for header, name in headers_env.items():
        if name not in os.environ:
            raise ValueError(f"Missing Prometheus credential environment variable: {name}")
        headers[header] = os.environ[name]
    step = float(raw.get("step_s", 15))
    if not math.isfinite(step) or step <= 0:
        raise ValueError("prometheus.step_s must be finite and positive")
    options = {
        key: raw[key]
        for key in ("timeout_ms", "max_response_bytes", "max_series", "connection_pool_maxsize")
        if key in raw
    }
    if any(isinstance(v, bool) or not isinstance(v, int) or v <= 0 for v in options.values()):
        raise ValueError("Prometheus limits must be positive integers")
    return PrometheusDataSource(
        str(raw["url"]), headers=headers, options=PrometheusOptions(**options)
    ), step


def parse_prometheus_probe(
    options: dict, service: Service, source: PrometheusDataSource | None, step_s: float
) -> PrometheusQueryProbe:
    options = dict(options)
    if source is None:
        raise ValueError("prometheus_query requires experiment prometheus.url")
    operation_name = options.pop("operation", None)
    if operation_name is not None and (not isinstance(operation_name, str) or not operation_name):
        raise ValueError("metric operation must be a nonempty name")
    operation = Operation(operation_name, service_name=service.name) if operation_name else None
    operation_metrics = options.pop("operation_metrics", None)
    qps_semantics = options.pop("qps_semantics", "completed")
    query_raw = options.pop("queries", [])
    queries = parse_queries(query_raw, service.name) if query_raw else []
    summaries = []
    for item in options.pop("summaries", []):
        definition = {key: value for key, value in item.items() if key != "window"}
        query = parse_queries([definition], service.name)[0]
        summaries.append(
            WindowQuery(
                query.name,
                query.promql,
                query.unit,
                query.description,
                query.labels,
                parse_window(item.get("window"), default="observation"),
            )
        )
    if options:
        raise ValueError(f"prometheus_query: unknown options: {sorted(options)}")
    return PrometheusQueryProbe(
        data_source=source,
        service=service.name,
        operation=operation,
        queries=queries,
        summaries=summaries,
        step_s=step_s,
        operation_metrics=operation_metrics,
        qps_semantics=qps_semantics,
    )


def parse_queries(items: object, service: str) -> list[PrometheusQuery]:
    if not isinstance(items, list) or not items:
        raise ValueError(
            f"observe[{service}].probes[prometheus_query] needs a non-empty `queries` list"
        )
    out: list[PrometheusQuery] = []
    seen = {"up"}
    for item in items:
        if not isinstance(item, dict):
            raise ValueError(
                f"observe[{service}].probes[prometheus_query].queries entries must be mappings"
            )
        name = item.get("name")
        promql = item.get("promql")
        if not isinstance(name, str) or not name:
            raise ValueError(f"Prometheus query needs a non-empty string `name`: {item!r}")
        if not isinstance(promql, str) or not promql:
            raise ValueError(f"Prometheus query {name!r} needs a non-empty string `promql`")
        if name in seen:
            raise ValueError(f"Prometheus query name {name!r} is duplicate or reserved")
        seen.add(name)
        kind = str(item.get("kind", "gauge"))
        if kind not in ("counter", "gauge"):
            raise ValueError(f"Prometheus query {name!r}: kind must be counter|gauge, got {kind!r}")
        labels = item.get("labels", [])
        if not (
            isinstance(labels, list)
            and all(isinstance(label, str) and label for label in labels)
            and len(set(labels)) == len(labels)
        ):
            raise ValueError(f"Prometheus query {name!r}: labels must be a list of unique names")
        unknown = set(item) - {"name", "promql", "kind", "unit", "description", "labels"}
        if unknown:
            raise ValueError(f"Prometheus query {name!r}: unknown keys {sorted(unknown)!r}")
        out.append(
            PrometheusQuery(
                name=name,
                promql=promql,
                value_kind=kind,  # type: ignore[arg-type]
                unit=str(item.get("unit", "")),
                description=str(item.get("description", "")),
                labels=tuple(labels),
            )
        )
    return out


def parse_columns(raw: dict | None) -> list[ReportColumn]:
    if raw is None:
        return []
    if not isinstance(raw, dict) or set(raw) != {"columns"} or not isinstance(raw["columns"], list):
        raise ValueError("report requires a columns list")
    columns = []
    titles = set()
    for item in raw["columns"]:
        if not isinstance(item, dict) or set(item) - {"title", "metric", "window"}:
            raise ValueError("Report columns require title, metric and optional window")
        if (
            not isinstance(item.get("title"), str)
            or not item["title"]
            or not isinstance(item.get("metric"), str)
            or not item["metric"]
        ):
            raise ValueError("Report column title and metric must be nonempty strings")
        if item["title"] in titles:
            raise ValueError("Report column titles must be unique")
        titles.add(item["title"])
        columns.append(
            ReportColumn(
                item["title"],
                item["metric"],
                parse_window(item.get("window"), default="measurement"),
            )
        )
    return columns


def parse_http_api_probe(
    options: dict,
    service: Service,
    profile: dict | None,
    source: PrometheusDataSource | None,
    step_s: float,
) -> PrometheusQueryProbe:
    from perf_harness.observe.http_api import HttpApiProbe, HttpMetricProfile

    if source is None:
        raise ValueError("http_api requires experiment prometheus.url")
    if not isinstance(profile, dict):
        raise ValueError("http_api requires observe[].http_metrics")
    if set(options) - {"operation", "metric_route"}:
        raise ValueError("http_api supports only operation and metric_route")
    operation = options.get("operation")
    if (
        not isinstance(operation, dict)
        or not {"name", "method", "path"} <= set(operation)
        or set(operation) - {"name", "method", "path", "response_mode"}
    ):
        raise ValueError("http_api operation requires name, method and path (route template)")
    if any(not isinstance(value, str) or not value for value in operation.values()):
        raise ValueError("HTTP operation name, method and path must be nonempty strings")
    declaration = dict(operation)
    mode = declaration.pop("response_mode", "unary")
    if mode not in {"unary", "sse"}:
        raise ValueError("HTTP operation response_mode must be unary or sse")
    operation_type = SSEOperation if mode == "sse" else HttpOperation
    try:
        metrics = HttpMetricProfile(**profile)
    except TypeError as exc:
        raise ValueError("http_metrics contains missing or unknown profile fields") from exc
    return HttpApiProbe(
        operation=operation_type(service_name=service.name, **declaration),
        profile=metrics,
        data_source=source,
        step_s=step_s,
        metric_route=options.get("metric_route"),
    )
