"""Configuration for direct metric scraping and explicit remote Prometheus queries."""

from typing import get_args

from prombed import ScrapeTarget

from perf_harness.model import ReportColumn, Service, WindowKind, WindowSelector
from perf_harness.observe.metric import MetricProbe, WindowQuery
from perf_harness.observe.prometheus import PrometheusQuery
from perf_harness.observe.prometheus_query import PrometheusQueryProbe


def parse_window(raw: dict | None, *, default: str) -> WindowSelector:
    raw = raw if raw is not None else {"kind": default}
    if not isinstance(raw, dict) or set(raw) - {"kind", "name", "level"}:
        raise ValueError("window must contain only kind, name and level")
    kind = raw.get("kind", default)
    if kind not in get_args(WindowKind):
        raise ValueError(f"slo.window/report.window: invalid kind: {kind!r}")
    if kind not in {"hold", "ramp", "warmup"} and ("name" in raw or "level" in raw):
        raise ValueError("window name/level only apply to load stages")
    return WindowSelector(
        kind, raw.get("name"), float(raw["level"]) if raw.get("level") is not None else None
    )


def parse_metric_probe(name: str, options: dict, service: Service):
    options = dict(options)
    headers = options.get("headers")
    if headers is not None and not isinstance(headers, dict):
        raise ValueError("headers must be a mapping")
    query_raw = options.pop("queries", [])
    queries = parse_queries(query_raw, service.name) if query_raw else []
    if name == "prometheus_query":
        allowed = {
            "url",
            "headers",
            "timeout_ms",
            "max_response_bytes",
            "max_series",
            "connection_pool_maxsize",
        }
        if not options.get("url") or not queries:
            raise ValueError("prometheus_query requires an explicit `url` and queries")
        if set(options) - allowed:
            raise ValueError(f"prometheus_query: unknown options: {sorted(set(options) - allowed)}")
        return PrometheusQueryProbe(service=service.name, queries=queries, **options)
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
    targets = options.pop("targets", [])
    parsed_targets = []
    for target in targets:
        if (
            not isinstance(target, dict)
            or set(target) - {"url", "instance"}
            or not target.get("url")
        ):
            raise ValueError("metric.targets entries need url and optional stable instance")
        parsed_targets.append(
            ScrapeTarget(
                str(target["url"]),
                labels={"instance": str(target.get("instance") or target["url"])},
            )
        )
    allowed = {
        "url",
        "path",
        "headers",
        "timeout_ms",
        "max_scrape_bytes",
        "retention_ms",
        "max_series",
        "max_samples_per_series",
        "connection_pool_maxsize",
    }
    if set(options) - allowed:
        raise ValueError(f"Unknown metric options: {sorted(set(options) - allowed)}")
    return MetricProbe(
        service=service.name,
        target_service=service,
        queries=queries,
        summaries=summaries,
        targets=tuple(parsed_targets),
        **options,
    )


def parse_queries(items: object, service: str) -> list[PrometheusQuery]:
    if not isinstance(items, list) or not items:
        raise ValueError(f"observe[{service}].probes[metric] needs a non-empty `queries` list")
    out: list[PrometheusQuery] = []
    seen = {"up"}
    for item in items:
        if not isinstance(item, dict):
            raise ValueError(f"observe[{service}].probes[metric].queries entries must be mappings")
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
