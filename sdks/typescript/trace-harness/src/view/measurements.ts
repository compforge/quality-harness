import type { Measurements } from "../model/measurement";
import type { TraceContext } from "../model/context";

export function measurementRows(measurements: Measurements, nodeId: string): Array<Record<string, unknown>> {
  const specs = new Map(measurements.specs.map((spec) => [spec.id, spec]));
  return (measurements.results[nodeId] ?? []).flatMap((result) => {
    const spec = specs.get(result.spec_id)!;
    const values = result.status !== "measured" ? { "": {} } : spec.dimensions.length ? result.values : { "": result.values };
    return Object.entries(values).map(([kind, metrics]) => ({ id: spec.id, scope: spec.scope, kind, status: result.status, values: metrics, units: spec.units, error: result.error, evidence: result.evidence }));
  });
}
export function measurementsMd(trace: TraceContext, measurements: Measurements): string {
  const lines = ["", "### Measurements", "", "Prefix = earliest observed trace start → node end; includes in-flight calls. Duration sums and kind coverage overlap; they are not causal contributions.", "", "| Node | Measurement | Scope | Kind | Values |", "| --- | --- | --- | --- | --- |"];
  for (const node of [...trace.nodes].sort((a, b) => a.end_ms - b.end_ms || a.node_id.localeCompare(b.node_id))) {
    for (const row of measurementRows(measurements, node.node_id)) {
      const units = row.units as Record<string, string>;
      const value = row.status === "measured" ? Object.entries(row.values as Record<string, unknown>).map(([key, value]) => `${key}=${value} ${units[key]}`).join(", ") : `${row.status}: ${row.error ?? ""}`;
      lines.push("| " + [node.node_id, row.id, row.scope, row.kind, value].map((v) => String(v).replaceAll("|", "\\|").replaceAll("\n", " ")).join(" | ") + " |");
    }
  }
  return lines.join("\n") + "\n";
}
