import type { DriveState } from "./scheduler";
import { stages, stageLabel, level, saturated } from "./load";
import type {
  ArmRun,
  RequestRecord,
  RequestStats,
  Window,
  DistributionSummary,
} from "./model";
const pct = (xs: number[], q: number) =>
  xs.length ? xs[Math.max(0, Math.ceil(q * xs.length) - 1)]! : 0;
export function requestStats(
  execution: ArmRun,
  start: number,
  end: number,
  select: (r: RequestRecord) => boolean = () => true,
): RequestStats {
  const records = execution.requests.filter(select),
    calls = new Map(execution.operation_runs.map((o) => [o.id, o.outcome]));
  const within = (t: number | undefined) =>
    t !== undefined && t >= start && t < end;
  const cohort = records.filter((r) => within(r.dispatched_at));
  const finished = cohort.filter((r) => r.state === "finished");
  const outputs = finished.map((r) => calls.get(r.operation_run_id!)!);
  const evaluations = finished.map(
    (r) => execution.evaluations[r.operation_run_id!],
  );
  const durs = outputs.map((o) => o.duration_ms).sort((a, b) => a - b);
  const arrived = records.filter((r) => within(r.scheduled_at));
  const completions = records.filter(
    (r) => r.state === "finished" && within(r.finished_at),
  );
  const succeeded = completions.filter(
    (r) => execution.evaluations[r.operation_run_id!]?.ok,
  ).length;
  const n = finished.length,
    n_ok = evaluations.filter((e) => e?.ok).length;
  const n_dropped = arrived.filter((r) => r.state === "dropped").length;
  const n_interrupted = cohort.filter((r) => r.state === "interrupted").length;
  const caveats: string[] = [];
  if (n && (saturated(execution.arm.load) || execution.windows.some(w => (w.limited_s ?? 0) > 0 && w.start_s < end && w.end_s > start))) caveats.push("co_biased");
  if (n_dropped) caveats.push("high_drop");
  if (n_interrupted || evaluations.some((e) => !e)) caveats.push("incomplete");
  if (n < 30) caveats.push("few_samples");
  const values: Record<string, number[]> = {};
  for (const o of outputs)
    for (const [key, value] of Object.entries(o.metrics ?? {}))
      (values[key] ??= []).push(value);
  values.scheduler_lag_ms = arrived.map(
    (r) => (r.arrived_at - r.scheduled_at) * 1000,
  );
  const metrics: Record<string, DistributionSummary> = {};
  for (const [key, vs] of Object.entries(values))
    if (vs.length) {
      vs.sort((a, b) => a - b);
      metrics[key] = {
        kind: "distribution",
        n: vs.length,
        mean: vs.reduce((a, b) => a + b, 0) / vs.length,
        p50: pct(vs, 0.5),
        p95: pct(vs, 0.95),
        p99: pct(vs, 0.99),
        caveats: [...caveats],
      };
    }
  let current = records.filter(
    (r) =>
      r.dispatched_at !== undefined &&
      r.dispatched_at < start &&
      (r.finished_at === undefined || r.finished_at >= start),
  ).length;
  let peak = current;
  const events: [number, number][] = [];
  for (const r of records) {
    if (within(r.dispatched_at)) events.push([r.dispatched_at!, 1]);
    if (r.dispatched_at !== undefined && within(r.finished_at))
      events.push([r.finished_at!, -1]);
  }
  for (const [, delta] of events.sort((a, b) => a[0] - b[0] || a[1] - b[1])) {
    current += delta;
    peak = Math.max(peak, current);
  }
  const error_breakdown: Record<string, number> = {};
  for (const e of evaluations)
    if (!e?.ok) {
      const key = e ? (e.error_kind ?? "unknown") : "unjudged";
      error_breakdown[key] = (error_breakdown[key] ?? 0) + 1;
    }
  const dt = Math.max(end - start, 1e-9);
  return {
    n,
    n_ok,
    throughput_rps: completions.length / dt,
    p50_ms: pct(durs, 0.5),
    p95_ms: pct(durs, 0.95),
    p99_ms: pct(durs, 0.99),
    mean_ms: n ? durs.reduce((a, b) => a + b, 0) / n : 0,
    error_rate: n ? (n - n_ok) / n : 0,
    error_breakdown,
    n_dropped,
    n_interrupted,
    arrived: arrived.length,
    dispatched: cohort.length,
    completed: completions.length,
    succeeded,
    arrival_rps: arrived.length / dt,
    dispatch_rps: cohort.length / dt,
    success_rps: succeeded / dt,
    inflight_peak: peak,
    inflight_end: current,
    caveats,
    metrics,
  };
}
export function buildWindows(execution: ArmRun, actualEnd: number, drive?: DriveState): Window[] {
  const load = execution.arm.load,
    warmup = drive?.measurement_start_s ?? 0;
  const make = (
    id: string,
    name: string,
    kind: Window["kind"],
    start: number,
    end: number,
    complete: boolean,
    target?: number,
  ): Window => {
    const by_case: Window["by_case"] = {},
      by_facet: Window["by_facet"] = {};
    for (const id of new Set(execution.requests.map((r) => r.case_id)))
      by_case[id] = requestStats(
        execution,
        start,
        end,
        (r) => r.case_id === id,
      );
    for (const key of new Set(
      execution.requests.flatMap((r) => Object.keys(r.facets)),
    )) {
      by_facet[key] = {};
      for (const value of new Set(
        execution.requests
          .filter((r) => key in r.facets)
          .map((r) => r.facets[key]!),
      ))
        by_facet[key]![value] = requestStats(
          execution,
          start,
          end,
          (r) => r.facets[key] === value,
        );
    }
    return {
      id,
      name,
      kind,
      start_s: start,
      end_s: end,
      complete,
      target_level: target,
      request: requestStats(execution, start, end),
      by_case,
      by_facet,
      probe_metrics: {},
    };
  };
  if (drive?.windows) execution.windows = drive.windows;
  const result = [
    make(
      "measurement",
      "measurement",
      "measurement",
      warmup,
      Math.max(warmup, actualEnd),
      drive
        ? drive.stop.reason === "deadline" && !!drive.windows?.some(w => w.kind === "hold" && w.complete)
        : actualEnd >= stages(load).reduce((n, s) => n + s.duration_s, 0),
    ),
  ];
  let clock = 0;
  (drive ? [] : stages(load)).forEach((stage, i) => {
    const start = Math.max(clock, warmup),
      end = Math.min(clock + stage.duration_s, actualEnd);
    if (end > start)
      result.push(
        make(
          `stage-${i}`,
          stageLabel(stage),
          stage.kind,
          start,
          end,
          actualEnd >= clock + stage.duration_s,
          level(stage),
        ),
      );
    clock += stage.duration_s;
  });
  if (drive?.windows) {
    result[0]!.limited_s = drive.windows
      .filter(w => w.kind === "hold")
      .reduce((n, w) => n + (w.limited_s ?? 0), 0);
    for (const w of drive.windows) {
      result.push({
        ...make(w.id, w.name, w.kind, w.start_s, w.end_s, w.complete, w.target_level),
        end_reason: w.end_reason, limited_s: w.limited_s,
      });
    }
  }
  const drainEnd = Math.max(
    actualEnd,
    ...execution.requests.map((r) => r.finished_at ?? actualEnd),
  );
  if (!drive && drainEnd > actualEnd)
    result.push(
      make("cooldown", "cooldown", "cooldown", actualEnd, drainEnd + 1e-9, true),
    );
  return result;
}
