import { intensityAt, pacingWait, scheduleDuration, type LoadProfile } from "./load";
import type { Case } from "@compforge/spec-case/model";
import type { Arm, Outcome, StopSnapshot, TimedOutcome, TrialStop } from "./model";
import { defaultJudge, type TrialContext, type Workload } from "./workload";

export interface DriveInput {
  workload: Workload;
  context: Omit<TrialContext, "signal" | "arm">;
  arm: Arm;
  cases: readonly Case[];
  weights: readonly number[];
  signal?: AbortSignal;
  now?: () => number;
}

export interface DriveResult {
  outcomes: TimedOutcome[];
  stop: TrialStop;
  elapsed_s: number;
}

interface Runtime {
  t0: number;
  now: () => number;
  load: LoadProfile;
  outcomes: TimedOutcome[];
  inFlight: number;
  dispatched: number;
  forced: boolean;
  stopScheduling: boolean;
  requestController: AbortController;
}

const delay = (ms: number): Promise<void> => new Promise((resolve) => setTimeout(resolve, ms));

function pick(cases: readonly Case[], weights: readonly number[]): Case {
  const total = weights.reduce((sum, value) => sum + value, 0);
  if (total <= 0) return cases[0]!;
  let cursor = Math.random() * total;
  for (let index = 0; index < cases.length; index += 1) {
    cursor -= weights[index] ?? 0;
    if (cursor <= 0) return cases[index]!;
  }
  return cases.at(-1)!;
}

function breaker(runtime: Runtime): StopSnapshot | undefined {
  const threshold = runtime.load.abort_on_error_rate;
  if (threshold === undefined) return undefined;
  const sent = runtime.outcomes.filter(({ outcome }) => !outcome.dropped);
  const minimum = runtime.load.breaker_min_n ?? 20;
  if (sent.length < minimum) return undefined;
  const errors = sent.filter(({ outcome }) => !outcome.ok).length;
  const errorRate = errors / sent.length;
  if (errorRate < threshold) return undefined;
  return {
    at_s: (runtime.now() - runtime.t0) / 1000,
    sent: sent.length,
    errors,
    error_rate: errorRate,
    threshold,
  };
}

function reserve(runtime: Runtime): boolean {
  if (runtime.stopScheduling) return false;
  if (runtime.load.max_requests !== undefined && runtime.dispatched >= runtime.load.max_requests) {
    runtime.stopScheduling = true;
    return false;
  }
  runtime.dispatched += 1;
  return true;
}

async function fireOne(
  input: DriveInput,
  runtime: Runtime,
  trial: Omit<TrialContext, "signal">,
  selected: Case,
): Promise<void> {
  if (!reserve(runtime)) return;
  const t = (runtime.now() - runtime.t0) / 1000;
  const started = runtime.now();
  runtime.inFlight += 1;
  let outcome: Outcome;
  try {
    outcome = await input.workload.fire({ ...trial, case: selected, signal: runtime.requestController.signal });
  } catch (error) {
    if (runtime.forced) return;
    outcome = {
      status: null,
      duration_ms: runtime.now() - started,
      meta: {
        exc: error instanceof Error ? error.name : "Error",
        exc_detail: error instanceof Error ? error.message : String(error),
      },
    };
  } finally {
    runtime.inFlight -= 1;
  }
  if (runtime.forced) return;
  const verdict = (input.workload.judge ?? defaultJudge)(outcome);
  outcome.ok = verdict.ok;
  outcome.error_kind = verdict.error_kind;
  outcome.case_id = selected.id;
  outcome.facets = { ...(selected.facets ?? {}), ...(outcome.facets ?? {}) };
  runtime.outcomes.push({ t, outcome });
}

async function windDown(tasks: Set<Promise<void>>, runtime: Runtime): Promise<Pick<TrialStop,
  "inflight_at_stop" | "interrupted" | "force_cancelled">> {
  const inflightAtStop = runtime.inFlight;
  const gracefulMs = Math.max(0, runtime.load.graceful_stop_s ?? 30) * 1000;
  if (tasks.size && gracefulMs > 0) {
    await Promise.race([Promise.allSettled([...tasks]), delay(gracefulMs)]);
  }
  const interrupted = runtime.inFlight;
  if (interrupted > 0) {
    runtime.forced = true;
    runtime.requestController.abort(new Error("perf trial graceful stop expired"));
    await Promise.allSettled([...tasks]);
  }
  return {
    inflight_at_stop: inflightAtStop,
    interrupted,
    force_cancelled: interrupted > 0,
  };
}

function track(tasks: Set<Promise<void>>, task: Promise<void>): void {
  tasks.add(task);
  task.finally(() => tasks.delete(task)).catch(() => undefined);
}

function stopReason(input: DriveInput, runtime: Runtime, snapshot?: StopSnapshot): TrialStop["reason"] {
  if (snapshot) return "error_rate";
  if (input.signal?.aborted) return "aborted";
  if (runtime.load.max_requests !== undefined && runtime.dispatched >= runtime.load.max_requests) {
    return "request_limit";
  }
  return "deadline";
}

async function driveClosed(input: DriveInput, runtime: Runtime, trial: Omit<TrialContext, "signal">): Promise<TrialStop> {
  const tasks = new Set<Promise<void>>();
  const deadline = runtime.t0 + scheduleDuration(runtime.load.schedule) * 1000;
  let desired = 0;
  let nextUserId = 0;
  let snapshot: StopSnapshot | undefined;

  const user = async (id: number): Promise<void> => {
    while (!runtime.stopScheduling && runtime.now() < deadline && !input.signal?.aborted) {
      if (id >= desired) {
        await delay(20);
        continue;
      }
      const started = runtime.now();
      await fireOne(input, runtime, trial, pick(input.cases, input.weights));
      const waitS = pacingWait(runtime.load.pacing, (runtime.now() - started) / 1000);
      // Even a zero-think-time user must yield so the supervisor can evaluate
      // the deadline/breaker instead of an immediately-resolved Workload
      // monopolising the microtask queue.
      await delay(waitS * 1000);
    }
  };

  while (runtime.now() < deadline && !input.signal?.aborted && !runtime.stopScheduling) {
    snapshot = breaker(runtime);
    if (snapshot) break;
    desired = Math.max(0, Math.round(intensityAt(runtime.load.schedule, (runtime.now() - runtime.t0) / 1000)));
    while (nextUserId < desired) {
      const task = user(nextUserId);
      nextUserId += 1;
      track(tasks, task);
    }
    await delay(20);
  }
  runtime.stopScheduling = true;
  return { reason: stopReason(input, runtime, snapshot), snapshot, ...await windDown(tasks, runtime) };
}

async function driveOpen(input: DriveInput, runtime: Runtime, trial: Omit<TrialContext, "signal">): Promise<TrialStop> {
  const tasks = new Set<Promise<void>>();
  const deadline = runtime.t0 + scheduleDuration(runtime.load.schedule) * 1000;
  let last = runtime.now();
  let accumulated = 0;
  let snapshot: StopSnapshot | undefined;
  while (runtime.now() < deadline && !input.signal?.aborted && !runtime.stopScheduling) {
    snapshot = breaker(runtime);
    if (snapshot) break;
    const now = runtime.now();
    accumulated += intensityAt(runtime.load.schedule, (now - runtime.t0) / 1000) * ((now - last) / 1000);
    last = now;
    while (accumulated >= 1 && !runtime.stopScheduling) {
      accumulated -= 1;
      const selected = pick(input.cases, input.weights);
      if (runtime.load.max_inflight !== undefined && runtime.inFlight >= runtime.load.max_inflight) {
        if (!reserve(runtime)) break;
        runtime.outcomes.push({
          t: (now - runtime.t0) / 1000,
          outcome: {
            status: null,
            duration_ms: 0,
            ok: false,
            error_kind: "client_saturated",
            dropped: true,
            case_id: selected.id,
            facets: { ...(selected.facets ?? {}) },
          },
        });
      } else {
        track(tasks, fireOne(input, runtime, trial, selected));
      }
    }
    await delay(10);
  }
  runtime.stopScheduling = true;
  return { reason: stopReason(input, runtime, snapshot), snapshot, ...await windDown(tasks, runtime) };
}

export async function drive(input: DriveInput): Promise<DriveResult> {
  const now = input.now ?? (() => performance.now());
  const runtime: Runtime = {
    t0: now(),
    now,
    load: input.arm.load,
    outcomes: [],
    inFlight: 0,
    dispatched: 0,
    forced: false,
    stopScheduling: false,
    requestController: new AbortController(),
  };
  const trial = { ...input.context, arm: input.arm };
  const stop = input.arm.load.model === "closed"
    ? await driveClosed(input, runtime, trial)
    : await driveOpen(input, runtime, trial);
  return { outcomes: runtime.outcomes, stop, elapsed_s: (now() - runtime.t0) / 1000 };
}
