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
  const counts = new Map<string, number>();
  let unjudged = 0;
  for (const record of completions) {
    const evaluation = execution.evaluations[record.operation_run_id!];
    if (evaluation === undefined) unjudged++;
    else counts.set(evaluation.code, (counts.get(evaluation.code) ?? 0) + 1);
  }
  const n = finished.length,
    n_ok = evaluations.filter(e => e?.code === "success").length,
    n_judged = evaluations.filter(e => e !== undefined).length;
  const interrupted = records.filter(r => r.state === "interrupted" && within(r.finished_at)).length;
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
  const errors = new Map<string, number>();
  for (const e of evaluations)
    if (e && e.code !== "success") errors.set(e.code, (errors.get(e.code) ?? 0) + 1);
  const error_breakdown = Object.fromEntries(errors);
  const dt = Math.max(end - start, 1e-9);
  return {
    n,
    n_ok,
    throughput_rps: completions.length / dt,
    p50_ms: pct(durs, 0.5),
    p95_ms: pct(durs, 0.95),
    p99_ms: pct(durs, 0.99),
    mean_ms: n ? durs.reduce((a, b) => a + b, 0) / n : 0,
    error_rate: n_judged ? (n_judged - n_ok) / n_judged : 0,
    n_judged,
    error_breakdown,
    n_dropped,
    n_interrupted,
    n_pending: cohort.filter(r => r.state === "dispatched").length,
    arrived: arrived.length,
    dispatched: cohort.length,
    completed: completions.length,
    succeeded: counts.get("success") ?? 0,
    completed_by_code: Object.fromEntries(counts),
    throughput_by_code: Object.fromEntries([...counts].map(([code, count]) => [code, count / dt])),
    unjudged,
    interrupted,
    arrival_rps: arrived.length / dt,
    dispatch_rps: cohort.length / dt,
    success_rps: (counts.get("success") ?? 0) / dt,
    unjudged_rps: unjudged / dt,
    inflight_peak: peak,
    inflight_end: current,
    caveats,
    metrics,
  };
}
export function buildWindows(execution: ArmRun, actualEnd: number, drive?: DriveState, ranges: import("./model").TimeRange[] = []): Window[] {
  const load = execution.arm.load;
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
  const segments = drive?.phases ?? [];
  if (!drive) {
    let clock = 0;
    stages(load).forEach((stage, index) => {
      const end = Math.min(clock + stage.duration_s, actualEnd);
      if (end > clock) segments.push({id: `stage-${index}`, name: stageLabel(stage), kind: stage.kind,
        start_s: clock, end_s: end, complete: end >= clock + stage.duration_s, target_level: level(stage)});
      clock += stage.duration_s;
    });
    const end = Math.max(actualEnd, ...execution.requests.map(r => r.finished_at ?? actualEnd));
    segments.push({id: "cooldown", name: "cooldown", kind: "cooldown", start_s: actualEnd, end_s: end + 1e-12, complete: true});
  }
  const phases: import("./model").PhaseRun[] = [];
  for (const segment of segments) {
    const previous = phases.at(-1);
    if (previous?.kind === segment.kind) {
      previous.end_s = segment.end_s;
      previous.complete &&= segment.complete;
      previous.limited_s = (previous.limited_s ?? 0) + (segment.limited_s ?? 0);
      previous.end_reason = segment.end_reason;
      if (previous.target_level !== segment.target_level) previous.target_level = undefined;
    } else phases.push({...segment, id: segment.kind, name: segment.kind});
  }
  execution.phases = [...execution.phases.filter(p => p.kind === "setup"), ...phases,
    ...execution.phases.filter(p => p.kind === "cleanup")];
  const start = phases[0]?.start_s ?? 0, end = phases.at(-1)?.end_s ?? start;
  const result = [make("full", "Full observation", "full", start, end, phases.length > 0 && phases.every(p => p.complete))];
  result[0]!.limited_s = phases.reduce((n,p) => n + (p.limited_s ?? 0), 0);
  for (const p of phases) result.push({...make(p.id, p.name, "phase", p.start_s, p.end_s, p.complete, p.target_level),
    phase: p.kind, end_reason: p.end_reason, limited_s: p.limited_s});
  ranges.forEach((range, index) => {
    const left = Math.max(start, range.start_s), right = Math.min(end, range.end_s);
    if (right > left) result.push(make(`custom-${index}`, range.name, "custom", left, right,
      range.start_s >= start && range.end_s <= end));
  });
  return result;
}
