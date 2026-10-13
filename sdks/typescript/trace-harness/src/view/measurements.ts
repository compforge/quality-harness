import { Measurements, type Measurement } from "../model/measurement";
import type { TraceContext } from "../model/context";

import type { Node } from "../model/node";

/** Pure report selection; must not compute facts, measurements or findings. */
export type MeasurementFilter = (node: Node, measurement: Measurement, trace: TraceContext) => boolean;

export function filterMeasurements(trace: TraceContext, measurements: Measurements, predicate?: MeasurementFilter): Measurements {
  if (!predicate) return measurements;
  const results: Record<string, Measurement[]> = {};
  for (const node of trace.nodes) {
    const selected = (measurements.nodes[node.node_id] ?? []).filter((value) => predicate(node, value, trace));
    if (selected.length) results[node.node_id] = selected;
  }
  return new Measurements(measurements.specs, measurements.sources, results, measurements.trace);
}

export function measurementRows(measurements: Measurements, nodeId: string | null): Array<Record<string, unknown>> {
  const specs = new Map(measurements.specs.map((spec) => [spec.kind, spec]));
  return (nodeId === null ? measurements.trace : measurements.nodes[nodeId] ?? []).flatMap((result) => {
    const spec = specs.get(result.kind)!;
    const selected = result.kind === "call_breakdown" ? { window: result.values.window } : result.values;
    const values = result.status !== "measured" ? { "": {} } : spec.dimensions.length ? selected : { "": selected };
    return Object.entries(values).map(([call_kind, metrics]) => ({ kind: spec.kind, scope: spec.scope, call_kind, status: result.status, values: metrics, units: spec.units, error: result.error, evidence: result.evidence }));
  });
}
export function measurementsMd(trace: TraceContext, measurements: Measurements): string {
  if (!Object.values(measurements.nodes).some((results) => results.length)) return "";
  const lines = ["", "### Measurements", "", "Prefix = earliest observed trace start → node end; includes in-flight calls. Duration sums and kind coverage overlap; they are not causal contributions.", "", "| Node | Measurement | Scope | Kind | Values |", "| --- | --- | --- | --- | --- |"];
  for (const node of [...trace.nodes].sort((a, b) => a.end_ms - b.end_ms || a.node_id.localeCompare(b.node_id))) {
    for (const row of measurementRows(measurements, node.node_id)) {
      const units = row.units as Record<string, string>;
      const value = row.status === "measured" ? Object.entries(row.values as Record<string, unknown>).map(([key, value]) => `${key}=${value} ${units[key]}`).join(", ") : `${row.status}: ${row.error ?? ""}`;
      lines.push("| " + [node.node_id, row.kind, row.scope, row.call_kind, value].map((v) => String(v).replaceAll("|", "\\|").replaceAll("\n", " ")).join(" | ") + " |");
    }
  }
  return lines.join("\n") + "\n";
}
