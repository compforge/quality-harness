import { randomUUID } from "node:crypto";
import { mkdirSync, writeFileSync, readFileSync, renameSync } from "node:fs";
import { join } from "node:path";
import type { MetricDBReader } from "@compforge/harness-toolbox/metricdb";
import type { Run } from "../model";
import { loadRun, serializeRun } from "../runio";
import { serializeSeries } from "../series";
import { collectMetrics, type CollectionOptions } from "./history";

export function archiveMetricCollection(run: Run, directory: string): string {
  const folder = join(directory,"metric-collections");
  mkdirSync(folder,{recursive:true});
  const path = join(folder,`${Date.now()}-${randomUUID()}.json`);
  writeFileSync(path,JSON.stringify({run_id:run.run_id, executions: Object.fromEntries(run.executions.map(arm =>
    [arm.id,arm.prometheus_observations ?? []]))},null,2),{flag:"wx"});
  return path;
}
/** Only metric projections change; request and evaluation files remain byte-for-byte intact. */
export function writeMetricData(run: Run, directory: string): void {
  const path = join(directory,"run.json");
  const saved = JSON.parse(readFileSync(path,"utf8"));
  if (saved.run_id !== run.run_id || saved.schema !== run.schema) throw new Error("Metric update requires the same saved Run");
  const executions = serializeRun(run).executions as Record<string, unknown>[];
  const byId = new Map(executions.map(arm => [arm.id,arm]));
  if (saved.executions.length !== byId.size || saved.executions.some((arm: {id:string}) => !byId.has(arm.id)))
    throw new Error("Metric update execution identity mismatch");
  for (const arm of saved.executions) {
    const updated = byId.get(arm.id)!;
    arm.prometheus_observations = updated.prometheus_observations;
    arm.probe_errors = updated.probe_errors;
    for (const window of arm.windows) {
      const current = (updated.windows as Run["executions"][number]["windows"]).find(item => item.id === window.id);
      if (!current || current.start_s !== window.start_s || current.end_s !== window.end_s) throw new Error("Metric update window mismatch");
      window.probe_metrics = current.probe_metrics;
    }
  }
  const temporary = `${path}.${randomUUID()}.tmp`;
  writeFileSync(temporary,JSON.stringify(saved,null,2),{flag:"wx"});
  renameSync(temporary,path);
  if (run.executions.some(arm => arm.prometheus_observations?.some(q => q.step_s !== null)))
    writeFileSync(join(directory,"timeseries.csv"),serializeSeries(run.executions));
}
/** Reuse frozen source identity, expressions and windows without loading project Runners. */
export async function recollectMetrics(directory: string, sources: readonly MetricDBReader[], options: CollectionOptions = {}): Promise<Run> {
  const run = loadRun(directory);
  if (!run.executions.some(arm => arm.prometheus_observations?.length)) throw new Error("Run has no saved metric queries");
  archiveMetricCollection(run,directory);
  try { await collectMetrics(run,sources,options); }
  finally {
    archiveMetricCollection(run,directory);
    writeMetricData(run,directory);
  }
  return run;
}
