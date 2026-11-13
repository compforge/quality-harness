"""Render numeric CallGraphSnapshot data without interpreting causality."""

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

from perf_harness.callgraph import CallGraphSnapshot

_COLORS = ("#dbeafe", "#dcfce7", "#fef3c7", "#ede9fe", "#fce7f3", "#cffafe")


def _value(metric: dict) -> str:
    return "N/A" if metric["value"] is None else f"{metric['value']:.3g}"


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
    frames = []
    for arm in snapshot.arms:
        for window in arm.windows:
            nodes = []
            for node in window.nodes:
                props = node.to_dict()["properties"]["perf"]
                metrics = props["metrics"]
                qps, p95 = _value(metrics["qps"]), _value(metrics["p95"])
                details = [
                    ("Operation", node.id),
                    ("Service", props["service"]),
                    ("HTTP", f"{props['method']} {props['path']}".strip()),
                    ("Window", f"{window.id}: {window.start_s:.3f}–{window.end_s:.3f}s"),
                    ("QPS semantics", props["qps_semantics"] or "unbound"),
                ]
                for role, metric in metrics.items():
                    details.append(
                        (role, f"{_value(metric)} {metric['unit']} ({metric['status']})")
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
                        f"{props['name']}\n{props['service']}\nQPS {qps} · p95 {p95} s",
                        props["service"],
                        details,
                        colors[props["service"]],
                    )
                )
            frames.append(
                GraphFrame(
                    f"{arm.arm_id} / {window.id} ({window.kind})",
                    nodes,
                    edges,
                    table=metric_tables.get((arm.id, window.id)) if metric_tables else None,
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
                "Latency percentiles cannot be summed along paths."
            ),
            GraphView("Operation metrics", frames, table_title="指标明细"),
        ],
    )
