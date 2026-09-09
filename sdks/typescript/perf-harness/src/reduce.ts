import type { LoadProfile } from "./load";
import { scheduleDuration } from "./load";
import type {
  DistributionSummary,
  Outcome,
  RequestStats,
  TimedOutcome,
  Window,
} from "./model";

const FEW_SAMPLES = 30;

function percentile(sorted: number[], quantile: number): number {
  if (!sorted.length) return 0;
  return sorted[Math.min(sorted.length - 1, Math.floor(quantile * sorted.length))]!;
}

function distribution(values: number[], closed: boolean): DistributionSummary {
  const sorted = [...values].sort((left, right) => left - right);
  const caveats = [closed ? "co_biased" : undefined, sorted.length < FEW_SAMPLES ? "few_samples" : undefined]
    .filter((value): value is string => !!value);
  return {
    kind: "distribution",
    n: sorted.length,
    mean: sorted.reduce((sum, value) => sum + value, 0) / sorted.length,
    p50: percentile(sorted, 0.5),
    p95: percentile(sorted, 0.95),
    p99: percentile(sorted, 0.99),
    caveats,
  };
}

export function requestStats(outcomes: Outcome[], durationS: number, closed: boolean): RequestStats {
  const sent = outcomes.filter((outcome) => !outcome.dropped);
  const dropped = outcomes.length - sent.length;
  const durations = sent.map((outcome) => outcome.duration_ms).sort((left, right) => left - right);
  const nOk = sent.filter((outcome) => outcome.ok).length;
  const errors: Record<string, number> = {};
  for (const outcome of sent) {
    if (!outcome.ok) errors[outcome.error_kind ?? "unknown"] = (errors[outcome.error_kind ?? "unknown"] ?? 0) + 1;
  }
  const metricNames = new Set(sent.flatMap((outcome) => Object.keys(outcome.metrics ?? {})));
  const metrics: Record<string, DistributionSummary> = {};
  for (const name of metricNames) {
    const values = sent.flatMap((outcome) => outcome.metrics?.[name] === undefined ? [] : [outcome.metrics[name]!]);
    if (values.length) metrics[name] = distribution(values, closed);
  }
  const caveats = [
    closed && sent.length ? "co_biased" : undefined,
    sent.length > 0 && sent.length < FEW_SAMPLES ? "few_samples" : undefined,
    dropped / Math.max(outcomes.length, 1) >= 0.01 ? "high_drop" : undefined,
  ].filter((value): value is string => !!value);
  return {
    n: sent.length,
    n_ok: nOk,
    throughput_rps: sent.length / Math.max(durationS, 1e-9),
    p50_ms: percentile(durations, 0.5),
    p95_ms: percentile(durations, 0.95),
    p99_ms: percentile(durations, 0.99),
    mean_ms: durations.length ? durations.reduce((sum, value) => sum + value, 0) / durations.length : 0,
    error_rate: sent.length ? (sent.length - nOk) / sent.length : 0,
    error_breakdown: errors,
    n_dropped: dropped,
    caveats,
    metrics,
  };
}

function facetStats(outcomes: Outcome[], durationS: number, closed: boolean): Window["by_facet"] {
  const result: Window["by_facet"] = {};
  const keys = new Set(outcomes.flatMap((outcome) => Object.keys(outcome.facets ?? {})));
  for (const key of keys) {
    const values = new Set(outcomes.flatMap((outcome) => outcome.facets?.[key] ? [outcome.facets[key]!] : []));
    result[key] = {};
    for (const value of values) {
      result[key]![value] = requestStats(
        outcomes.filter((outcome) => outcome.facets?.[key] === value),
        durationS,
        closed,
      );
    }
  }
  return result;
}

function caseStats(outcomes: Outcome[], durationS: number, closed: boolean): Window["by_case"] {
  const result: Window["by_case"] = {};
  const caseIds = new Set(outcomes.flatMap((outcome) => outcome.case_id ? [outcome.case_id] : []));
  for (const caseId of caseIds) {
    result[caseId] = requestStats(
      outcomes.filter((outcome) => outcome.case_id === caseId),
      durationS,
      closed,
    );
  }
  return result;
}

export function buildWindows(load: LoadProfile, timed: TimedOutcome[], actualEndS: number): Window[] {
  const plannedEnd = scheduleDuration(load.schedule);
  const warmup = load.warmup_s ?? 0;
  const measurementStart = warmup;
  const measurementEnd = Math.max(warmup, Math.min(actualEndS, plannedEnd));
  const make = (
    id: string,
    name: string,
    kind: Window["kind"],
    start: number,
    end: number,
    complete: boolean,
    targetLevel?: number,
  ): Window => {
    const outcomes = timed.filter((item) => item.t >= start && item.t < end).map((item) => item.outcome);
    return {
      id,
      name,
      kind,
      start_s: start,
      end_s: end,
      complete,
      target_level: targetLevel,
      request: requestStats(outcomes, Math.max(end - start, 1e-9), load.model === "closed"),
      by_case: caseStats(outcomes, Math.max(end - start, 1e-9), load.model === "closed"),
      by_facet: facetStats(outcomes, Math.max(end - start, 1e-9), load.model === "closed"),
      probe_metrics: {},
    };
  };
  const windows: Window[] = [make(
    "measurement",
    "measurement",
    "measurement",
    measurementStart,
    measurementEnd,
    actualEndS >= plannedEnd,
  )];
  let cursor = 0;
  load.schedule.stages.forEach((stage, index) => {
    const start = Math.max(cursor, warmup);
    const plannedStageEnd = cursor + stage.over_s;
    const end = Math.min(plannedStageEnd, actualEndS);
    if (end > start) {
      windows.push(make(
        `stage-${String(index + 1).padStart(2, "0")}`,
        stage.name ?? (stage.kind === "hold" ? `hold@${stage.to_level}` : `ramp→${stage.to_level}`),
        stage.kind,
        start,
        end,
        actualEndS >= plannedStageEnd,
        stage.to_level,
      ));
    }
    cursor = plannedStageEnd;
  });
  return windows;
}
