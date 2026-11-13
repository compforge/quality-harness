"""Render numeric CallGraphSnapshot data without interpreting causality."""

import math

from harness_common import RelationKind
from harness_common.report_kit import (
    GraphEdge,
    GraphFrame,
    GraphNode,
    GraphView,
    Prose,
    Section,
    Table,
)

from perf_harness.callgraph import CallGraphSnapshot, CallGraphWindow

_COLORS = ("#dbeafe", "#dcfce7", "#fef3c7", "#ede9fe", "#fce7f3", "#cffafe")


def _value(metric: dict) -> str:
    return (
        "N/A"
        if metric["value"] is None
        else "+Inf"
        if metric["value"] == "+Inf"
        else f"{metric['value']:.3g}"
    )


# @spec Size encodes QPS within the selected window, independently of latency and missing data.
_MIN_DIAMETER = 88.0
_MAX_DIAMETER = 160.0
_DEFAULT_DIAMETER = 112.0


def _node_diameters(window: CallGraphWindow) -> dict[str, float]:
    values = {
        node.id: value
        for node in window.nodes
        if isinstance(value := node.properties["perf"]["metrics"]["qps"]["value"], (int, float))
        and math.isfinite(value)
        and value >= 0
    }
    sizes = {node.id: _DEFAULT_DIAMETER for node in window.nodes}
    if not values:
        return sizes
    low, high = min(values.values()), max(values.values())
    if low == high:
        return sizes
    for ident, value in values.items():
        # @why Interpolate area rather than radius so large traffic is not visually squared.
        ratio = (value - low) / (high - low)
        sizes[ident] = math.sqrt(_MIN_DIAMETER**2 + ratio * (_MAX_DIAMETER**2 - _MIN_DIAMETER**2))
    return sizes


def section(
    snapshot: CallGraphSnapshot,
    metric_tables: dict[tuple[str, str], Table] | None = None,
) -> Section:
    colors = {
        name: _COLORS[index % len(_COLORS)]
        for index, name in enumerate(
            sorted(
                {
                    str(node.properties["perf"]["service"])
                    for arm in snapshot.arms
                    for window in arm.windows
                    for node in window.nodes
                }
            )
        )
    }
    edges = [
        GraphEdge(
            r.id,
            r.source,
            r.target,
            "may_call" if r.kind == RelationKind.MAY_CALL else "call",
            r.kind == RelationKind.MAY_CALL,
        )
        for r in snapshot.relations
    ]
    incoming = {edge.target for edge in edges}
    frames = []
    for arm in snapshot.arms:
        for window in arm.windows:
            nodes = []
            diameters = _node_diameters(window)
            for node in window.nodes:
                props = node.to_dict()["properties"]["perf"]
                metrics = props["metrics"]
                qps, p95 = _value(metrics["qps"]), _value(metrics["p95"])
                error = metrics["error_rate"]["value"]
                error_text = "N/A" if error is None else f"{error:.1%}"
                details = [
                    ("Operation", node.id),
                    ("Importance", props["importance"]),
                    ("Service", props["service"]),
                    ("Path", props["path"] or "N/A"),
                    ("Method", props["method"] or "N/A"),
                    ("Response mode", props["response_mode"] or "unbound"),
                    ("Window", f"{window.id}: {window.start_s:.3f}–{window.end_s:.3f}s"),
                    ("QPS semantics", props["qps_semantics"] or "unbound"),
                ]
                for role, metric in metrics.items():
                    details.append(
                        (role, f"{_value(metric)} {metric['unit']} ({metric['status']})")
                    )
                    if metric.get("semantics"):
                        details.append((role + " semantics", metric["semantics"]))
                    if metric.get("histogram"):
                        details.extend(
                            (key, str(value)) for key, value in metric["histogram"].items()
                        )
                    if metric["description"]:
                        details.append((role + " description", metric["description"]))
                    if metric["reason"]:
                        details.append((role + " reason", metric["reason"]))
                    if metric["metric_ref"]:
                        details.append((role + " metric", metric["metric_ref"]))
                    for query in metric["queries"]:
                        details.append(("PromQL", query["expression"]))
                        if query["error"]:
                            details.append(("Query error", query["error"]))
                if metrics["qps"]["value"] is not None and metrics["qps"]["value"] > 0:
                    details.append(("Traffic", "Observed on this node; incoming edge not verified"))
                nodes.append(
                    GraphNode(
                        node.id,
                        f"{props['name']}\nQPS {qps}\np95 {p95} s\nerr {error_text}",
                        props["service"],
                        details,
                        colors[props["service"]],
                        tooltip="\n".join(f"{key}: {value}" for key, value in details),
                        diameter=diameters[node.id],
                        importance=props["importance"],
                    )
                )
            frames.append(
                GraphFrame(
                    f"{arm.arm_id} / {window.name} [{window.start_s:.3f}–{window.end_s:.3f}s]",
                    nodes,
                    edges,
                    entry_ids=[node.id for node in nodes if node.id not in incoming],
                    table=metric_tables.get((arm.id, window.id)) if metric_tables else None,
                    group={"full": "全程观测", "phase": "指定阶段", "custom": "自定义范围"}[
                        window.kind
                    ],
                )
            )
    scope = (
        f"Case: {snapshot.case_ids[0]}"
        if len(snapshot.case_ids) == 1
        else "Aggregate case scope; metrics are not attributed to individual cases."
    )
    return Section(
        "Call Graph",
        [
            Prose(scope),
            Prose(
                "Operation nodes; service is auxiliary information. Solid: call, dashed: may_call. "
                "Metrics describe the selected server/window, including other matching traffic. "
                "Node traffic does not prove a particular edge executed. "
                "N/A means missing observation, not zero. "
                "Latency percentiles cannot be summed along paths. "
                "Circle area scales with the current window’s QPS range; "
                "missing QPS uses the default size. Hover for details."
            ),
            GraphView(
                "Operation metrics",
                frames,
                table_title="完整窗口指标明细",
                group_filter_label="Service",
                importance_filter=True,
            ),
        ],
    )
