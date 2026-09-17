import { LoadController } from "./controller";
import type {
  Arm,
  ArmRun,
  ArmStop,
  Case,
  Outcome,
  RequestRecord,
  StopSnapshot,
  Window,
  Phase,
} from "./model";
import type { ArmContext, Runner } from "./runner";
import type { Judge } from "./judge";

// Owned by the ArmRun lifecycle, so exceptions cannot discard already observed facts.
export interface DriveState {
  measurement_end_s?: number;
  measurement_start_s?: number;
  windows?: Window[];
  phase?: Phase;
  stop: ArmStop;
}

export async function drive(options: {
  state: DriveState;
  runner: Runner;
  judge: Judge;
  context: ArmContext;
  arm: Arm;
  cases: readonly Case[];
  weights: readonly number[];
  execution: ArmRun;
  signal?: AbortSignal;
}): Promise<void> {
  const {
      runner,
      judge,
      context,
      arm,
      cases,
      weights,
      execution,
      signal,
      state,
    } = options,
    load = arm.load;
  const start = performance.now(),
    now = () => (performance.now() - start) / 1000;
  const active = new Map<Promise<void>, AbortController>();
  const errors: unknown[] = [];
  let completed = 0,
    failed = 0,
    due = 0;
  let lastDispatch: number | undefined, lastRate: number | undefined;
  let wasFull = false;
  const control = new LoadController(load);
  state.windows = control.windows;
  let reason: ArmStop["reason"] = "deadline",
    snapshot: StopSnapshot | undefined;
  let rngState = (load.seed ?? 0) >>> 0;
  const random = () => {
    rngState = (Math.imul(1664525, rngState) + 1013904223) >>> 0;
    return (rngState + 0.5) / 4294967296;
  };
  const pick = () => {
    let value = random() * weights.reduce((a, b) => a + b, 0);
    for (let i = 0; i < cases.length; i++) {
      value -= weights[i]!;
      if (value < 0) return cases[i]!;
    }
    return cases[cases.length - 1]!;
  };
  function offer(scheduled: number): boolean {
    const at = now();
    if (at >= control.deadline || active.size >= control.target(at)[1]) return false;
    const item = pick();
    const record: RequestRecord = {
      id: `${execution.id}:${execution.requests.length}`,
      case_id: item.id,
      scheduled_at: scheduled,
      arrived_at: now(),
      state: "arrived",
      facets: { ...item.facets },
    };
    execution.requests.push(record);
    const controller = new AbortController();
    record.dispatched_at = now();
    record.operation_run_id = record.id;
    record.state = "dispatched";
    // Promise callbacks start after the synchronous reservation below.
    const task = Promise.resolve()
      .then(async () => {
        let outcome: Outcome;
        const fireContext = {
          ...context,
          case: item,
          signal: controller.signal,
        };
        let operation = { name: runner.name };
        try {
          operation = runner.operation?.(fireContext) ?? operation;
          outcome = await runner.fire(fireContext);
        } catch (error) {
          outcome = {
            status: null,
            duration_ms: (now() - record.dispatched_at!) * 1000,
            meta: controller.signal.aborted
              ? { interrupted: true }
              : {
                  exc: error instanceof Error ? error.name : "Error",
                  exc_detail: String(error),
                },
          };
        }
        record.finished_at = now();
        record.state = controller.signal.aborted ? "interrupted" : "finished";
        if (record.state === "interrupted") record.reason = "cancelled";
        outcome = {
          ...outcome,
          case_id: item.id,
          facets: { ...item.facets, ...outcome.facets },
        };
        record.facets = outcome.facets!;
        execution.operation_runs.push({
          id: record.id,
          service: context.service,
          operation,
          outcome,
        });
        if (record.state === "finished") {
          const evaluation = judge(outcome);
          execution.evaluations[record.id] = evaluation;
          completed++;
          if (!evaluation.ok) failed++;
        }
      })
      .catch((error) => {
        errors.push(error);
      })
      .finally(() => {
        active.delete(task);
      });
    active.set(task, controller);
    return true;
  }
  // Resolve on completion or deadline, with no orphaned timers/listeners.
  async function wait(seconds: number): Promise<void> {
    let timer: ReturnType<typeof setTimeout> | undefined;
    let abort: (() => void) | undefined;
    try {
      await Promise.race([
        ...active.keys(),
        new Promise<void>((resolve) => {
          timer = setTimeout(resolve, Math.max(0, seconds) * 1000);
        }),
        new Promise<void>((resolve) => {
          abort = () => resolve();
          signal?.addEventListener("abort", abort, { once: true });
          if (signal?.aborted) resolve();
        }),
      ]);
    } finally {
      if (timer !== undefined) clearTimeout(timer);
      if (abort) signal?.removeEventListener("abort", abort);
    }
  }
  try {
    while (!signal?.aborted) {
      const elapsed = now();
      control.advance(
        elapsed,
        active.size,
        active.size >= control.target(elapsed)[1] && elapsed >= due,
      );
      state.phase = control.phase;
      if (errors.length) throw errors[0];
      if (control.done) break;
      if (
        load.abort_on_error_rate !== undefined &&
        completed >= (load.breaker_min_n ?? 20) &&
        failed / completed >= load.abort_on_error_rate
      ) {
        reason = "error_rate";
        snapshot = {
          at_s: elapsed,
          completed,
          errors: failed,
          error_rate: failed / completed,
          threshold: load.abort_on_error_rate,
        };
        control.abort(elapsed);
        break;
      }
      const [rate, cap] = control.target(elapsed);
      if (rate !== lastRate) {
        due = lastDispatch === undefined
          ? elapsed
          : Math.max(elapsed, lastDispatch + (rate ? 1 / rate : Infinity));
        lastRate = rate;
      }
      if (wasFull && active.size < cap) due = Math.max(due, elapsed);
      wasFull = active.size >= cap;
      if (active.size < cap && rate && due <= elapsed) {
        // No queued arrivals while full, and no catch-up burst after a scheduler stall.
        if (!offer(due)) continue;
        lastDispatch = elapsed;
        due = elapsed + (load.arrival === "poisson" ? -Math.log(random()) / rate : 1 / rate);
        control.advance(now(), active.size);
        await new Promise<void>((resolve) => setImmediate(resolve));
        continue;
      }
      // Observe a blocked pacing opportunity even when no request has completed yet.
      const delay = Math.max(0, Math.min(
        0.01,
        control.deadline - now(),
        rate && due > now() ? due - now() : Infinity,
      ));
      await wait(delay);
      await new Promise<void>((resolve) => setImmediate(resolve));
    }
    if (signal?.aborted) {
      reason = "aborted";
      control.abort(now());
    }
    state.measurement_end_s = control.end_s!;
    state.measurement_start_s = control.hold_start_s ?? control.end_s!;
    state.stop = {
      reason, snapshot, inflight_at_stop: active.size,
      interrupted: 0, force_cancelled: false,
    };
    state.phase = "cooldown";
    const deadline = now() + (load.cooldown_timeout_s ?? 180);
    while (!signal?.aborted && active.size && now() < deadline)
      await wait(deadline - now());
    if (signal?.aborted) state.stop.reason = "aborted";
  } finally {
    // Capture the load boundary before cancellation; cleanup latency is not measurement.
    if (state.measurement_end_s === undefined) {
      control.abort(now());
      state.measurement_end_s = control.end_s!;
      state.measurement_start_s = control.hold_start_s ?? control.end_s!;
      state.stop = {
        reason: "aborted", inflight_at_stop: active.size,
        interrupted: 0, force_cancelled: false,
      };
    }
    for (const controller of active.values()) controller.abort();
    await Promise.all(active.keys());
    state.stop.interrupted = execution.requests.filter((r) => r.state === "interrupted").length;
    state.stop.force_cancelled = state.stop.interrupted > 0;
    state.windows!.push({
      id: "cooldown", name: "cooldown", kind: "cooldown",
      start_s: state.measurement_end_s,
      end_s: now(),
      complete: !state.stop.force_cancelled,
      end_reason: state.stop.force_cancelled
        ? (state.stop.reason === "aborted" ? "cancelled" : "timeout")
        : "empty",
      limited_s: 0,
      by_case: {}, by_facet: {}, probe_metrics: {},
    });
  }
  if (errors.length) throw errors[0];
}
