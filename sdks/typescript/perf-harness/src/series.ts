import { existsSync, readFileSync } from "node:fs";
import type { ArmRun } from "./model";

// Perf CSV string fields can contain quoted label sets and commas. Label newlines
// are escaped by the series ID encoder.
export function loadSeries(path: string, arms: Map<string, ArmRun>): void {
  if (!existsSync(path)) return;
  const lines = readFileSync(path, "utf8").trimEnd().split(/\r?\n/);
  for (const row of lines.slice(1)) {
    const fields = Array.from(
      (row + ",").matchAll(/(?:"((?:[^"]|"")*)"|([^,]*)),/g),
      (m) => (m[1] ?? m[2]!).replaceAll('""', '"'),
    );
    if (fields.length !== 4) throw new Error("Invalid perf timeseries row");
    const [armId, id, t, value] = fields as [string, string, string, string];
    const arm = arms.get(armId);
    if (!arm) continue;
    const series = (arm.series ??= Object.create(null));
    (series[id] ??= []).push([Number(t), Number(value)]);
  }
}
export function serializeSeries(arms: ArmRun[]): string {
  const quote = (v: string) => '"' + v.replaceAll('"', '""') + '"';
  const rows = ["arm_run,series,t,value"];
  for (const a of arms)
    for (const [id, points] of Object.entries(a.series ?? {}))
      for (const [t, v] of points)
        rows.push([quote(a.id), quote(id), t, v].join(","));
  return rows.join("\n") + "\n";
}
