import { createHash } from "node:crypto";
import {
  validateCaseSet,
  type Case,
  type CaseSet,
} from "@compforge/spec-case/model";
import { defaultJudge, type Judge } from "./judge";
import { drive, type DriveState } from "./scheduler";
import { buildWindows } from "./reduce";
import {
  loadLabel,
  serializeLoadPlan,
  resourceLabel,
  validateLoadPlan,
  type LoadPlan,
} from "./load";
import type {
  Arm,
  CaseMixEntry,
  Phase,
  PhaseError,
  ResourceProfile,
  Run,
  Service,
  ArmRun,
} from "./model";
import type { ArmContext, Runner } from "./runner";

export interface Experiment {
  name?: string;
  service: Service;
  runner: Runner;
  judge?: Judge;
  resources?: ResourceProfile[];
  loads: LoadPlan[];
  caseSet?: CaseSet;
  caseMix?: readonly CaseMixEntry[];
  signal?: AbortSignal;
  onArmStart?(context: ArmContext, startedAt: Date): Promise<void> | void;
  onArmFinish?(arm_run: ArmRun): Promise<void> | void;
}

function resolveCases(experiment: Experiment): {
  cases: readonly Case[];
  weights: number[];
} {
  if (!experiment.caseSet) {
    if (experiment.caseMix?.length)
      throw new Error("perf caseMix requires a canonical caseSet");
    return { cases: [{ id: "default", input: {} }], weights: [1] };
  }
  validateCaseSet(experiment.caseSet);
  const byId = new Map(experiment.caseSet.cases.map((item) => [item.id, item]));
  if (!byId.size)
    throw new Error(
      `perf CaseSet '${experiment.caseSet.caseset}' has no cases`,
    );
  const selection = experiment.caseMix?.length
    ? experiment.caseMix
    : experiment.caseSet.cases.map((item) => ({ id: item.id, weight: 1 }));
  const ids = new Set<string>();
  const cases: Case[] = [];
  const weights: number[] = [];
  for (const entry of selection) {
    if (ids.has(entry.id))
      throw new Error(`duplicate perf Case selection: ${entry.id}`);
    ids.add(entry.id);
    const item = byId.get(entry.id);
    if (!item) {
      throw new Error(
        `perf Case '${entry.id}' not found in CaseSet '${experiment.caseSet.caseset}'`,
      );
    }
    const weight = entry.weight ?? 1;
    if (!Number.isFinite(weight) || weight < 0) {
      throw new Error(`perf Case weight must be finite and >= 0: ${entry.id}`);
    }
    cases.push(item);
    weights.push(weight);
  }
  if (!weights.some((weight) => weight > 0)) {
    throw new Error("perf case mix requires at least one positive weight");
  }
  return { cases, weights };
}

function runId(now = new Date()): string {
  const pad = (value: number): string => String(value).padStart(2, "0");
  return `${now.getFullYear()}${pad(now.getMonth() + 1)}${pad(now.getDate())}-${pad(now.getHours())}${pad(now.getMinutes())}${pad(now.getSeconds())}`;
}

function armFingerprint(resources: ResourceProfile, load: LoadPlan): string {
  // Object insertion order and omitted defaults do not create a different Arm.
  const canonical = (value: unknown): unknown => {
    if (Array.isArray(value)) return value.map(canonical);
    if (value && typeof value === "object")
      return Object.fromEntries(
        Object.entries(value)
          .sort(([a], [b]) => a.localeCompare(b))
          .map(([key, item]) => [key, canonical(item)]),
      );
    return value;
  };
  const config = {
    resources: { replicas: 1, extra: {}, ...resources },
    load: serializeLoadPlan(load),
  };
  return createHash("sha256")
    .update(JSON.stringify(canonical(config)))
    .digest("hex")
    .slice(0, 8);
}

function arms(experiment: Experiment): Arm[] {
  const resources = experiment.resources?.length ? experiment.resources : [{}];
  const expanded = resources.flatMap((resource) =>
    experiment.loads.map((load) => ({
      base: `${resourceLabel(resource)}|${loadLabel(load)}`,
      resources: resource,
      load,
    })),
  );
  const counts = new Map<string, number>();
  for (const item of expanded)
    counts.set(item.base, (counts.get(item.base) ?? 0) + 1);
  const resolved = expanded.map((item) => {
    const suffix =
      counts.get(item.base)! > 1
        ? `@${armFingerprint(item.resources, item.load)}`
        : "";
    return {
      id: `${item.base}${suffix}`,
      resources: item.resources,
      load: item.load,
    };
  });
  const ids = resolved.map((arm) => arm.id);
  // One Arm executes once per Run. A content hash cannot identify a repetition.
  if (new Set(ids).size !== ids.length)
    throw new Error(`duplicate arm id: ${ids.join(", ")}`);
  return resolved;
}

function phaseError(phase: Phase, error: unknown): PhaseError {
  return {
    phase,
    error_type: error instanceof Error ? error.name : "Error",
    message: error instanceof Error ? error.message : String(error),
  };
}

class ArmExecutionContext {
  phase: Phase = "setup";
  readonly drive: DriveState = {
    stop: {
      reason: "aborted",
      inflight_at_stop: 0,
      interrupted: 0,
      force_cancelled: false,
    },
  };
  readonly phaseErrors: PhaseError[] = [];
  hasFatalError = false;
  fatalError: unknown;
  hasCleanupAfterFatal = false;
  cleanupAfterFatal: unknown;

  constructor(readonly runner: ArmContext) {}

  enter(phase: Phase): void {
    this.phase = phase;
  }

  record(error: unknown, phase = this.phase): void {
    this.phaseErrors.push(phaseError(phase, error));
  }
}

export class Engine {
  readonly #experiment: Experiment;
  readonly #runId: string;
  readonly #cases: readonly Case[];
  readonly #weights: number[];

  constructor(experiment: Experiment, options: { run_id?: string } = {}) {
    if (!experiment.loads.length)
      throw new Error("perf experiment requires at least one load profile");
    experiment.loads.forEach(validateLoadPlan);
    const resolved = resolveCases(experiment);
    this.#experiment = experiment;
    this.#runId = options.run_id ?? runId();
    this.#cases = resolved.cases;
    this.#weights = resolved.weights;
  }

  async run(): Promise<Run> {
    const created = new Date();
    const arm_runs: ArmRun[] = [];
    const resolvedArms = arms(this.#experiment);
    for (const arm of resolvedArms) {
      if (this.#experiment.signal?.aborted) break;
      const started = new Date();
      const controller = new AbortController();
      const abort = () => controller.abort(this.#experiment.signal?.reason);
      this.#experiment.signal?.addEventListener("abort", abort, { once: true });
      const context: ArmContext = {
        service: this.#experiment.service,
        arm,
        run_id: this.#runId,
        signal: controller.signal,
      };
      const execution = new ArmExecutionContext(context);
      const result: ArmRun = {
        id: `${this.#runId}:${arm.id}`,
        service: context.service.name,
        arm,
        started_at: started.toISOString(),
        finished_at: "",
        windows: [],
        stop: {
          reason: "aborted",
          inflight_at_stop: 0,
          interrupted: 0,
          force_cancelled: false,
        },
        slo: [],
        registry: {},
        probe_errors: {},
        phase_errors: [],
        operation_runs: [],
        requests: [],
        evaluations: {},
      };
      try {
        await this.#experiment.runner.setup?.(execution.runner);
        execution.enter("warmup");
        await this.#experiment.onArmStart?.(execution.runner, started);
        await drive({
          state: execution.drive,
          judge: this.#experiment.judge ?? defaultJudge,
          execution: result,
          runner: this.#experiment.runner,
          context: execution.runner,
          arm,
          cases: this.#cases,
          weights: this.#weights,
          signal: this.#experiment.signal,
        });
        execution.enter("cooldown");
        await this.#experiment.runner.deactivate?.(execution.runner);
      } catch (error) {
        if (this.#experiment.signal?.aborted) {
          execution.hasFatalError = true;
          execution.fatalError = error;
        } else {
          if (execution.phase === "warmup" || execution.phase === "hold") execution.enter(execution.drive.phase ?? execution.phase);
          execution.record(error);
        }
      } finally {
        this.#experiment.signal?.removeEventListener("abort", abort);
        try {
          await this.#experiment.runner.cleanup?.(execution.runner);
        } catch (cleanupError) {
          if (execution.hasFatalError) {
            execution.hasCleanupAfterFatal = true;
            execution.cleanupAfterFatal = cleanupError;
          } else execution.record(cleanupError, "cleanup");
        }
      }
      if (execution.hasFatalError) {
        if (execution.hasCleanupAfterFatal) {
          throw new AggregateError(
            [execution.fatalError, execution.cleanupAfterFatal],
            "perf arm_run was cancelled and cleanup also failed",
            { cause: execution.fatalError },
          );
        }
        throw execution.fatalError;
      }

      const finished = new Date();
      const arm_run = result;
      arm_run.finished_at = finished.toISOString();
      arm_run.windows = buildWindows(
        arm_run,
        execution.drive.measurement_end_s ?? 0,
        execution.drive,
      );
      arm_run.stop = execution.drive.stop;
      arm_run.phase_errors = execution.phaseErrors;
      arm_runs.push(arm_run);
      await this.#experiment.onArmFinish?.(arm_run);
      // A phase failure means the execution/testbed state is no longer a safe
      // baseline for the next Arm, regardless of any future SLO stop policy.
      if (arm_run.phase_errors.length) break;
    }
    return {
      schema: 6,
      run_id: this.#runId,
      experiment: this.#experiment.name ?? "perf",
      created_at: created.toISOString(),
      service: this.#experiment.service.name,
      passed:
        arm_runs.length === resolvedArms.length &&
        arm_runs.every(
          (arm_run) =>
            !arm_run.phase_errors.length &&
            arm_run.stop.reason === "deadline" &&
            !arm_run.stop.interrupted,
        ),
      executions: arm_runs,
      artifacts: [],
    };
  }
}
