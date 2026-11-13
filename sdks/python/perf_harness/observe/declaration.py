"""Resolve reusable observation YAML against Common targets and named data sources."""

from dataclasses import replace
from pathlib import Path
from urllib.parse import unquote

import yaml
from harness_common import (
    Graph,
    HttpOperation,
    NodeKind,
    Operation,
    RelationKind,
    SSEOperation,
    Workload,
)
from harness_toolbox.prometheus import PrometheusDataSource

from perf_harness.metric import series_id
from perf_harness.model import (
    HistogramQuantile,
    MetricBinding,
    ObservationBinding,
    Service,
    WindowSelector,
)
from perf_harness.observe.base import Probe
from perf_harness.observe.configuration import parse_prometheus, parse_queries
from perf_harness.observe.definition import ObservationSpec
from perf_harness.observe.http_api import HttpApiProbe, HttpMetricProfile
from perf_harness.observe.k8s import (
    KubectlTopProbe,
    PerWorkerRSSProbe,
    PodCountProbe,
    ResourceLimitsProbe,
    RestartProbe,
)
from perf_harness.observe.prometheus import WindowQuery
from perf_harness.observe.prometheus_query import PrometheusQueryProbe
from perf_harness.observe.registry import ProbeConfig, build_probe

_KUBE = {
    "top": KubectlTopProbe,
    "rss": PerWorkerRSSProbe,
    "pods": PodCountProbe,
    "limits": ResourceLimitsProbe,
    "restart": RestartProbe,
}


def operation_from(raw: dict) -> Operation:
    """Construct Common identity once; HTTP path is metadata, never the join key."""
    fields = dict(raw)
    mode = fields.pop("response_mode", "unary")
    if mode not in {"unary", "sse"}:
        raise ValueError("operation response_mode must be unary or sse")
    if "path" in fields or "method" in fields:
        cls = SSEOperation if mode == "sse" else HttpOperation
    else:
        if mode == "sse":
            raise ValueError("SSE operation requires method and path")
        cls = Operation
    try:
        operation = cls(**fields)
        operation.id()
    except (TypeError, ValueError) as exc:
        raise ValueError("invalid Common Operation declaration") from exc
    return operation


def load_observation(
    value: object,
    config_dir: Path,
    services: dict[str, Service],
    graph: Graph | None,
    data_sources: dict,
    windows: list[WindowSelector],
) -> list[ObservationSpec]:
    if isinstance(value, str):
        path = Path(value).expanduser()
        value = yaml.safe_load((path if path.is_absolute() else config_dir / path).read_text())
    if not isinstance(value, dict) or set(value) - {"operations", "observations"}:
        raise ValueError("observation requires a file or operations/observations mapping")
    operations: dict[str, Operation] = {}
    if graph:
        for node in graph.nodes:
            if node.kind == NodeKind.SERVICE:
                name = unquote(node.id.removeprefix("service:"))
                services.setdefault(
                    node.id, Service(name, environment=next(iter(services.values())).environment)
                )
            elif node.kind == NodeKind.OPERATION:
                owner = graph.relations_from(node.id, kind=RelationKind.BELONGS_TO)[0].target
                raw = {
                    "name": unquote(node.id.split("/", 1)[1]),
                    "service_name": unquote(owner.removeprefix("service:")),
                }
                raw.update(
                    {
                        key: node.properties[key]
                        for key in ("method", "path", "response_mode")
                        if key in node.properties
                    }
                )
                operation = operation_from(raw)
                if operation.id() != node.id:
                    raise ValueError(f"graph operation identity disagrees with owner: {node.id}")
                operations[node.id] = operation
    for raw in value.get("operations", []):
        operation = operation_from(raw)
        if operation.id() in operations:
            raise ValueError(f"duplicate operation declaration: {operation.id()}")
        operations[operation.id()] = operation
    sources: dict[str, tuple[PrometheusDataSource, float]] = {}
    for name, raw in data_sources.items():
        options = dict(raw)
        if options.pop("type", None) != "prometheus":
            raise ValueError(f"unsupported data source type for {name}")
        source, step = parse_prometheus(options)
        assert source is not None
        sources[name] = (source, step)
    declarations = value.get("observations")
    if not isinstance(declarations, list):
        raise ValueError("observation asset requires an observations list")
    result = []
    for item in declarations:
        if not isinstance(item, dict) or set(item) - {"target", "workload", "probes"}:
            raise ValueError("observation requires target, optional workload and probes")
        target = item.get("target")
        operation = operations.get(target)
        service_id = Service(operation.service_name).id() if operation else target
        if service_id not in services:
            raise ValueError(f"unknown observation target: {target}")
        service = services[service_id]
        workload = None
        if "workload" in item:
            workload = next((w for w in service.workloads if w.name == item["workload"]), None)
            if workload is None or operation:
                raise ValueError("workload observation requires a declared Service workload")
        probes = []
        for raw in item.get("probes", []):
            probes.append(_probe(raw, service, operation, workload, sources, windows))
        result.append(ObservationSpec(service, tuple(probes), operation, workload))
    return result


def _probe(
    raw: dict,
    service: Service,
    operation: Operation | None,
    workload: Workload | None,
    sources: dict[str, tuple[PrometheusDataSource, float]],
    windows: list[WindowSelector],
) -> Probe:
    if not isinstance(raw, dict):
        raise ValueError("observation probe must be a mapping")
    options = dict(raw)
    kind = options.pop("type", None)
    instance_name = options.pop("name", None)
    metrics = options.pop("metrics", {})
    per_pod = options.pop("per_pod", False)
    if not isinstance(kind, str) or not kind:
        raise ValueError("observation probe requires a nonempty type")
    if instance_name is not None and (not isinstance(instance_name, str) or not instance_name):
        raise ValueError("observation probe name must be a nonempty string")
    if not isinstance(metrics, dict) or not isinstance(per_pod, bool):
        raise ValueError("probe metrics must be a mapping and per_pod must be boolean")
    source_name = options.pop("data_source", None)
    if source_name is not None and source_name not in sources:
        raise ValueError(f"unknown observation data source: {source_name}")
    source, step = sources[source_name] if source_name else (None, 15)
    if per_pod and metrics:
        raise ValueError(
            "per_pod observations retain Pod series; no implicit aggregate metric binding"
        )
    if kind in {"prometheus_query", "http_api"}:
        if source is None:
            raise ValueError("Prometheus probe requires a named data_source")
        if per_pod:
            raise ValueError("Prometheus queries declare output labels, not per_pod")
        if kind == "http_api":
            if not isinstance(operation, HttpOperation) or workload:
                raise ValueError("http_api requires an HTTP Operation target")
            if metrics:
                raise ValueError("http_api supplies its metrics from the profile")
            probe = HttpApiProbe(
                operation=operation,
                profile=HttpMetricProfile(**options.pop("profile")),
                data_source=source,
                step_s=step,
                metric_route=options.pop("metric_route", None),
            )
            # One project declaration; this experiment selects the windows.
            templates = [q for q in probe.summaries if q.window.kind == "full"]
            probe.summaries = [replace(q, window=w) for w in windows for q in templates]
        else:
            summaries = []
            for name, spec in metrics.items():
                if not isinstance(spec, dict) or set(spec) - {
                    "promql",
                    "unit",
                    "description",
                    "semantics",
                    "histogram",
                }:
                    raise ValueError(f"invalid query metric declaration: {name}")
                if not isinstance(spec.get("promql"), str) or not spec["promql"]:
                    raise ValueError(f"metric {name} requires promql")
                summaries.extend(
                    WindowQuery(
                        name,
                        spec["promql"],
                        unit=spec.get("unit", ""),
                        description=spec.get("description", ""),
                        window=w,
                        histogram=HistogramQuantile(**spec["histogram"])
                        if "histogram" in spec
                        else None,
                    )
                    for w in windows
                )
            queries = options.pop("queries", [])
            probe = PrometheusQueryProbe(
                data_source=source,
                service=service.name,
                operation=operation,
                summaries=summaries,
                queries=parse_queries(queries, service.name) if queries else [],
                step_s=step,
            )
    elif kind in _KUBE:
        if operation:
            raise ValueError("Kubernetes probes observe Service or Workload, not Operation")
        probe = _KUBE[kind](target_service=service, target_workload=workload, per_pod=per_pod)
    else:
        # Keep the existing project extension registry; do not add a second plugin mechanism.
        probe = build_probe(
            kind,
            ProbeConfig(
                service=service,
                operation=operation,
                workload=workload,
                windows=tuple(windows),
                prometheus=source,
                step_s=step,
                per_pod=per_pod,
                options=options,
            ),
        )
        options = {}
    if options:
        raise ValueError(f"unknown {kind} probe options: {sorted(options)}")
    if instance_name:
        probe.name += "." + instance_name
    if workload:
        probe.workload_name = workload.name
        if kind not in _KUBE:
            probe.name += "." + workload.name
    if metrics:
        bindings = {}
        for name, spec in metrics.items():
            if kind == "prometheus_query":
                ref, stat = name, "value"
            else:
                if set(spec) - {"metric", "description", "semantics"}:
                    raise ValueError("producer metrics select metric/stat and optional semantics")
                ref, stat = spec["metric"].rsplit(".", 1)
            family = probe.families[ref]
            bindings[name] = MetricBinding(
                series_id(f"{probe.family}.{ref}", probe.labels) + "." + stat,
                family.unit,
                spec.get("description", family.description),
                spec.get("semantics", ""),
            )
        probe.observation_binding = ObservationBinding(
            target=operation.id() if operation else service.id(),
            service=service.name,
            metrics=bindings,
            workload=workload.name if workload else None,
            method=operation.method if isinstance(operation, HttpOperation) else "",
            path=operation.path if isinstance(operation, HttpOperation) else "",
            response_mode=operation.response_mode if isinstance(operation, HttpOperation) else None,
        )
    return probe
