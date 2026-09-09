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
            m for m in measurements.results.get(node.node_id, ()) if predicate(node, m, trace)
        ]
        if selected:
            results[node.node_id] = selected
    return Measurements(measurements.specs, measurements.sources, results)


def measurement_rows(measurements: Measurements, node_id: str) -> list[dict]:
    specs = {spec.id: spec for spec in measurements.specs}
    rows = []
    for result in measurements.results.get(node_id, ()):
        spec = specs[result.spec_id]
        values = result.values if spec.dimensions else {"": result.values}
        if result.status != "measured":
            values = {"": {}}
        for kind, metrics in values.items():
            rows.append(
                {
                    "id": spec.id,
                    "scope": spec.scope,
                    "kind": kind,
                    "status": result.status,
                    "values": metrics,
                    "units": spec.units,
                    "error": result.error,
                    "evidence": result.evidence,
                }
            )
    return rows


def measurements_md(trace: Renderable, measurements: Measurements | None) -> str:
    if measurements is None or not any(measurements.results.values()):
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
                    escape(v) for v in (node.node_id, row["id"], row["scope"], row["kind"], value)
                )
                + " |"
            )
    return "\n".join(lines) + "\n"
