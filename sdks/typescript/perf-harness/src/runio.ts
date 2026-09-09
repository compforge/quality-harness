import { mkdirSync, writeFileSync } from "node:fs";
import { join } from "node:path";
import type { LoadProfile } from "./load";
import type { Outcome, RequestStats, ResourceProfile, Run, TrialRecord } from "./model";

function resourceJson(resource: ResourceProfile): Record<string, unknown> {
  return {
    cpu: resource.cpu ?? null,
    memory: resource.memory ?? null,
    workers: resource.workers ?? null,
    replicas: resource.replicas ?? 1,
    extra: resource.extra ?? {},
  };
}

function loadJson(load: LoadProfile): Record<string, unknown> {
  const pacing = load.pacing ?? { kind: "none" as const };
  return {
    model: load.model,
    schedule: load.schedule,
    pacing: {
      kind: pacing.kind,
      secs: "secs" in pacing ? pacing.secs : 0,
      max_secs: "max_secs" in pacing ? pacing.max_secs : 0,
    },
    warmup_s: load.warmup_s ?? 0,
    max_inflight: load.max_inflight ?? null,
    max_requests: load.max_requests ?? null,
    abort_on_error_rate: load.abort_on_error_rate ?? null,
    breaker_min_n: load.breaker_min_n ?? 20,
    graceful_stop_s: load.graceful_stop_s ?? 30,
  };
}

function statsJson(stats: RequestStats | undefined): Record<string, unknown> | null {
  if (!stats) return null;
  return { ...stats, metrics: stats.metrics };
}

function trialJson(trial: TrialRecord): Record<string, unknown> {
  return {
    id: trial.id,
    service: trial.service,
    started_at: trial.started_at,
    finished_at: trial.finished_at,
    arm: {
      id: trial.arm.id,
      resources: resourceJson(trial.arm.resources),
      load: loadJson(trial.arm.load),
    },
    stop: trial.stop,
    slo: trial.slo,
    registry: trial.registry,
    windows: trial.windows.map((window) => ({
      ...window,
      request: statsJson(window.request),
    })),
    probe_errors: trial.probe_errors,
    phase_errors: trial.phase_errors,
  };
}

function outcomeJson(trial: TrialRecord, t: number, outcome: Outcome): Record<string, unknown> {
  return {
    trial: trial.id,
    t: Math.round(t * 1000) / 1000,
    case_id: outcome.case_id ?? "",
    status: outcome.status,
    duration_ms: outcome.duration_ms,
    ok: outcome.ok ?? false,
    error_kind: outcome.error_kind ?? null,
    events: outcome.events ?? 0,
    nbytes: outcome.nbytes ?? 0,
    dropped: outcome.dropped ?? false,
    facets: outcome.facets ?? {},
    metrics: outcome.metrics ?? {},
    meta: outcome.meta ?? {},
  };
}

export function serializeRun(run: Run): Record<string, unknown> {
  return {
    schema: 4,
    run_id: run.run_id,
    experiment: run.experiment,
    created_at: run.created_at,
    service: run.service,
    passed: run.passed,
    n_trials: run.n_trials,
    trials: run.trials.map(trialJson),
  };
}

export function serializeOutcomes(run: Run): string {
  const lines = run.trials.flatMap((trial) => trial.outcomes.map(({ t, outcome }) => (
    JSON.stringify(outcomeJson(trial, t, outcome))
  )));
  return lines.length ? `${lines.join("\n")}\n` : "";
}

/** Cross-harness verdict: this implementation has no SLO API yet, so a complete run is observed, not judged. */
export function serializeVerdict(run: Run): Record<string, unknown> {
  const errorTrial = run.trials.find((trial) => trial.phase_errors.length);
  const failed = run.trials.find((trial) => (
    trial.stop.reason === "error_rate" || trial.stop.reason === "aborted"
  ));
  const firstError = errorTrial?.phase_errors[0];
  return {
    schema_version: 1,
    harness: "perf",
    scope: run.experiment,
    run_id: run.run_id,
    status: errorTrial ? "error" : failed ? "fail" : "skipped",
    ...(firstError
      ? { reason: `trial ${errorTrial.id} ${firstError.phase}: ${firstError.error_type}: ${firstError.message}` }
      : failed ? { reason: `trial ${failed.id} stopped: ${failed.stop.reason}` } : {}),
    artifact_paths: { run: "run.json", raw: "outcomes.jsonl" },
    created_at: run.created_at,
  };
}

export function writeRunData(
  run: Run,
  directory: string,
): { run_json: string; outcomes: string; verdict: string } {
  mkdirSync(directory, { recursive: true });
  const runJson = join(directory, "run.json");
  const outcomes = join(directory, "outcomes.jsonl");
  const verdict = join(directory, "verdict.json");
  writeFileSync(runJson, `${JSON.stringify(serializeRun(run), null, 2)}\n`);
  writeFileSync(outcomes, serializeOutcomes(run));
  writeFileSync(verdict, `${JSON.stringify(serializeVerdict(run), null, 2)}\n`);
  return { run_json: runJson, outcomes, verdict };
}
