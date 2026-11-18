import type { ArmRun, ReportColumn, Window } from "../model";
import { seriesId } from "../observe/metric";
import { matches } from "../observe/history";

function metricValue(arm: ArmRun, w: Window, ref: string): string {
  const end = ref.lastIndexOf(".");
  const address = ref.slice(0, end),
    stat = ref.slice(end + 1);
  const family = address.split("{")[0]!;
  const labels: Record<string, string> = Object.create(null);
  for (const m of address
    .slice(family.length)
    .matchAll(/(?:[{,]\s*)([\w]+)\s*=\s*("(?:[^"\\]|\\.)*")/g))
    labels[m[1]!] = JSON.parse(m[2]!);
  const summary = w.probe_metrics[seriesId(family, labels)];
  let value: unknown;
  if (summary) value = Reflect.get(summary, stat);
  else if (labels.service) {
    const probe = [family.split(".")[0], labels.service, labels.operation]
      .filter(Boolean)
      .join(".");
    return Object.hasOwn(arm.probe_errors, probe)
      ? "missing (probe_error)"
      : "missing (no_slice)";
  } else {
    const { code, ...facets } = labels;
    const pairs = Object.entries(facets),
      pair = pairs[0];
    const stats = pair
      ? pairs.length === 1 && w.by_facet[pair[0]]?.[pair[1]]
      : w.request;
    if (!stats) return "missing (no_slice)";
    const field = family.replace(/^request\./, "");
    if (
      code &&
      (field === "completed_by_code" || field === "throughput_by_code")
    )
      value = stats[field][code];
    else if (field === "duration_ms" && stats.n)
      value = stat === "n" ? stats.n : Reflect.get(stats, stat + "_ms");
    else if (stat === "value" && field === "drop_rate")
      value = stats.arrived ? stats.n_dropped / stats.arrived : 0;
    else if (stat === "value" && (field !== "error_rate" || stats.n_judged))
      value = Reflect.get(stats, field);
    else if (stats.metrics[family])
      value = Reflect.get(stats.metrics[family]!, stat);
  }
  return typeof value === "number"
    ? String(Number(value.toPrecision(4)))
    : value === "+Inf"
      ? "+Inf"
      : "missing (no_data)";
}
export function selectedCells(arm: ArmRun, columns: ReportColumn[]): string[] {
  return columns.map((column) => {
    const windows = arm.windows.filter((w) => matches(w, column));
    return (
      windows
        .map(
          (w) =>
            (windows.length > 1 ? w.id + ": " : "") +
            metricValue(arm, w, column.metric) +
            (w.complete ? "" : " (incomplete)"),
        )
        .join("; ") || "missing (no window)"
    );
  });
}
