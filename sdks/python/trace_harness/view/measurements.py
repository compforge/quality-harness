"""Pure Measurement projections shared by interactive and Markdown output."""

from collections.abc import Callable

from trace_harness.model.context import TraceContext
from trace_harness.model.ir import Renderable
from trace_harness.model.measurement import Measurement, Measurements
from trace_harness.model.node import Node

# A presentation predicate; it must not compute facts, measurements or findings.
MeasurementFilter = Callable[[Node, Measurement, TraceContext], bool]


def filter_measurements(
    trace: TraceContext, measurements: Measurements, predicate: MeasurementFilter | None
) -> Measurements:
    """Select report rows without changing the analysis or its shared evidence."""
    if predicate is None:
        return measurements
    results = {}
    for node in trace.nodes:
        selected = [
            m for m in measurements.nodes.get(node.node_id, ()) if predicate(node, m, trace)
        ]
        if selected:
            results[node.node_id] = selected
    return Measurements(measurements.specs, measurements.sources, results, measurements.trace)


def measurement_rows(measurements: Measurements, node_id: str | None) -> list[dict]:
    specs = {spec.kind: spec for spec in measurements.specs}
    rows = []
    for result in measurements.trace if node_id is None else measurements.nodes.get(node_id, ()):
        spec = specs[result.kind]
        selected = (
            {"window": result.values.get("window")}
            if result.kind == "call_breakdown"
            else result.values
        )
        values = selected if spec.dimensions else {"": selected}
        if result.status != "measured":
            values = {"": {}}
        for kind, metrics in values.items():
            rows.append(
                {
                    "kind": spec.kind,
                    "scope": spec.scope,
                    "call_kind": kind,
                    "status": result.status,
                    "values": metrics,
                    "units": spec.units,
                    "error": result.error,
                    "evidence": result.evidence,
                }
            )
    return rows


def measurements_md(trace: Renderable, measurements: Measurements | None) -> str:
    if measurements is None or not any(measurements.nodes.values()):
        return ""
    lines = [
        "",
        "### Measurements",
        "",
        "Prefix = earliest observed trace start → node end; includes in-flight calls. "
        "Duration sums and kind coverage overlap; they are not causal contributions.",
        "",
        "| Node | Measurement | Scope | Kind | Values |",
        "| --- | --- | --- | --- | --- |",
    ]

    def escape(value: object) -> str:
        return str(value).replace("|", "\\|").replace("\n", " ")

    for node in sorted(trace.nodes, key=lambda node: (node.end_ms, node.node_id)):
        for row in measurement_rows(measurements, node.node_id):
            value = (
                ", ".join(
                    f"{key}={value} {row['units'][key]}" for key, value in row["values"].items()
                )
                if row["status"] == "measured"
                else f"{row['status']}: {row['error'] or ''}"
            )
            lines.append(
                "| "
                + " | ".join(
                    escape(v)
                    for v in (node.node_id, row["kind"], row["scope"], row["call_kind"], value)
                )
                + " |"
            )
    return "\n".join(lines) + "\n"
