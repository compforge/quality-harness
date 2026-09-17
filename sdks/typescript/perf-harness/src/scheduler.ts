import { arrivalTime, saturated, target } from "./load";
import type {
  Arm,
  ArmRun,
  ArmStop,
  Case,
  Outcome,
  RequestRecord,
  StopSnapshot,
} from "./model";
import type { ArmContext, Runner } from "./runner";
import type { Judge } from "./judge";

export async function drive(options: {
  runner: Runner;
  judge: Judge;
  context: ArmContext;
  arm: Arm;
  cases: readonly Case[];
  weights: readonly number[];
  execution: ArmRun;
  signal?: AbortSignal;
}): Promise<{ stop: ArmStop; elapsed_s: number }> {
  const { runner, judge, context, arm, cases, weights, execution, signal } =
      options,
    load = arm.load;
  const start = performance.now(),
    now = () => (performance.now() - start) / 1000;
  const active = new Map<Promise<void>, AbortController>();
  const errors: unknown[] = [];
  let completed = 0,
    failed = 0,
    volume = 0,
    due = saturated(load) ? 0 : arrivalTime(load, 0);
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
  function offer(scheduled: number, dropReason?: string): void {
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
    if (dropReason || active.size >= target(load, now())[1]) {
      record.state = "dropped";
      record.reason = dropReason ?? "concurrency_limit";
      record.finished_at = now();
      return;
    }
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
    while (now() < load.duration_s && !signal?.aborted) {
      if (errors.length) throw errors[0];
      if (
        load.abort_on_error_rate !== undefined &&
        completed >= (load.breaker_min_n ?? 20) &&
        failed / completed >= load.abort_on_error_rate
      ) {
        reason = "error_rate";
        snapshot = {
          at_s: now(),
          completed,
          errors: failed,
          error_rate: failed / completed,
          threshold: load.abort_on_error_rate,
        };
        break;
      }
      if (saturated(load)) {
        const count = Math.max(0, target(load, now())[1] - active.size);
        for (let i = 0; i < count; i++) offer(now());
      } else if (due <= now()) {
        offer(due);
        volume += load.arrival === "poisson" ? -Math.log(random()) : 1;
        due = arrivalTime(load, volume);
        await new Promise<void>((resolve) => setImmediate(resolve));
        continue;
      }
      const delay = Math.max(
        0,
        Math.min(
          0.01,
          load.duration_s - now(),
          saturated(load) ? Infinity : due - now(),
        ),
      );
      await wait(delay);
      // Even immediately completed Runners must let timers and external aborts progress.
      await new Promise<void>((resolve) => setImmediate(resolve));
    }
    if (signal?.aborted) reason = "aborted";
    if (reason === "deadline" && !saturated(load)) {
      while (due < load.duration_s && !signal?.aborted) {
        offer(due, "scheduler_deadline");
        volume += load.arrival === "poisson" ? -Math.log(random()) : 1;
        due = arrivalTime(load, volume);
        await new Promise<void>((resolve) => setImmediate(resolve));
      }
    }
    const elapsed_s = reason === "deadline" ? load.duration_s : now();
    const inflight_at_stop = active.size;
    const drainDeadline = now() + (load.drain_timeout_s ?? 30);
    while (!signal?.aborted && active.size && now() < drainDeadline)
      await wait(drainDeadline - now());
    if (signal?.aborted) reason = "aborted";
    for (const controller of active.values()) controller.abort();
    // Runner must cooperate with cancellation; do not close its clients while work remains.
    await Promise.all(active.keys());
    if (errors.length) throw errors[0];
    const interrupted = execution.requests.filter(
      (r) => r.state === "interrupted",
    ).length;
    return {
      stop: {
        reason,
        snapshot,
        inflight_at_stop,
        interrupted,
        force_cancelled: interrupted > 0,
      },
      elapsed_s,
    };
  } finally {
    for (const controller of active.values()) controller.abort();
    await Promise.all(active.keys());
  }
}
