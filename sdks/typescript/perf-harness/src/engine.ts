import { createHash } from "node:crypto";
import { validateCaseSet, type Case, type CaseSet } from "@compforge/spec-case/model";
import { drive } from "./scheduler";
import { buildWindows } from "./reduce";
import { loadLabel, resourceLabel, validateLoadProfile, type LoadProfile } from "./load";
import type {
  Arm,
  CaseMixEntry,
  Phase,
  PhaseError,
  ResourceProfile,
  Run,
  Service,
  TrialRecord,
} from "./model";
import type { TrialContext, Workload } from "./workload";

export interface Experiment {
  name?: string;
  service: Service;
  workload: Workload;
  resources?: ResourceProfile[];
  loads: LoadProfile[];
  caseSet?: CaseSet;
  caseMix?: readonly CaseMixEntry[];
  signal?: AbortSignal;
  onTrialStart?(context: TrialContext, startedAt: Date): Promise<void> | void;
  onTrialFinish?(trial: TrialRecord): Promise<void> | void;
}

function resolveCases(experiment: Experiment): { cases: readonly Case[]; weights: number[] } {
  if (!experiment.caseSet) {
    if (experiment.caseMix?.length) throw new Error("perf caseMix requires a canonical caseSet");
    return { cases: [{ id: "default", input: {} }], weights: [1] };
  }
  validateCaseSet(experiment.caseSet);
  const byId = new Map(experiment.caseSet.cases.map((item) => [item.id, item]));
  if (!byId.size) throw new Error(`perf CaseSet '${experiment.caseSet.caseset}' has no cases`);
  const selection = experiment.caseMix?.length
    ? experiment.caseMix
    : experiment.caseSet.cases.map((item) => ({ id: item.id, weight: 1 }));
  const ids = new Set<string>();
  const cases: Case[] = [];
  const weights: number[] = [];
  for (const entry of selection) {
    if (ids.has(entry.id)) throw new Error(`duplicate perf Case selection: ${entry.id}`);
    ids.add(entry.id);
    const item = byId.get(entry.id);
    if (!item) {
      throw new Error(`perf Case '${entry.id}' not found in CaseSet '${experiment.caseSet.caseset}'`);
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

function arms(experiment: Experiment): Arm[] {
  const resources = experiment.resources?.length ? experiment.resources : [{}];
  const expanded = resources.flatMap((resource) => experiment.loads.map((load) => ({
    base: `${resourceLabel(resource)}|${loadLabel(load)}`,
    resources: resource,
    load,
  })));
  const counts = new Map<string, number>();
  for (const item of expanded) counts.set(item.base, (counts.get(item.base) ?? 0) + 1);
  return expanded.map((item) => {
    const suffix = counts.get(item.base)! > 1
      ? `@${createHash("sha256").update(JSON.stringify(item)).digest("hex").slice(0, 8)}`
      : "";
    return { id: `${item.base}${suffix}`, resources: item.resources, load: item.load };
  });
}

function phaseError(phase: Phase, error: unknown): PhaseError {
  return {
    phase,
    error_type: error instanceof Error ? error.name : "Error",
    message: error instanceof Error ? error.message : String(error),
  };
}

class TrialExecutionContext {
  phase: Phase = "setup";
  readonly phaseErrors: PhaseError[] = [];
  hasFatalError = false;
  fatalError: unknown;
  hasCleanupAfterFatal = false;
  cleanupAfterFatal: unknown;

  constructor(readonly workload: TrialContext) {}

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
    if (!experiment.loads.length) throw new Error("perf experiment requires at least one load profile");
    experiment.loads.forEach(validateLoadProfile);
    const resolved = resolveCases(experiment);
    this.#experiment = experiment;
    this.#runId = options.run_id ?? runId();
    this.#cases = resolved.cases;
    this.#weights = resolved.weights;
  }

  async run(): Promise<Run> {
    const created = new Date();
    const trials: TrialRecord[] = [];
    for (const arm of arms(this.#experiment)) {
      if (this.#experiment.signal?.aborted) break;
      const started = new Date();
      const controller = new AbortController();
      const abort = () => controller.abort(this.#experiment.signal?.reason);
      this.#experiment.signal?.addEventListener("abort", abort, { once: true });
      const context: TrialContext = {
        service: this.#experiment.service,
        arm,
        run_id: this.#runId,
        signal: controller.signal,
      };
      const execution = new TrialExecutionContext(context);
      let driven: Awaited<ReturnType<typeof drive>> | undefined;
      try {
        await this.#experiment.workload.setup?.(execution.workload);
        execution.enter("measurement");
        await this.#experiment.onTrialStart?.(execution.workload, started);
        driven = await drive({
          workload: this.#experiment.workload,
          context: { service: execution.workload.service, run_id: execution.workload.run_id },
          arm,
          cases: this.#cases,
          weights: this.#weights,
          signal: this.#experiment.signal,
        });
        execution.enter("deactivate");
        await this.#experiment.workload.deactivate?.(execution.workload);
      } catch (error) {
        if (this.#experiment.signal?.aborted) {
          execution.hasFatalError = true;
          execution.fatalError = error;
        }
        else execution.record(error);
      } finally {
        this.#experiment.signal?.removeEventListener("abort", abort);
        try {
          await this.#experiment.workload.cleanup?.(execution.workload);
        } catch (cleanupError) {
          if (execution.hasFatalError) {
            execution.hasCleanupAfterFatal = true;
            execution.cleanupAfterFatal = cleanupError;
          }
          else execution.record(cleanupError, "cleanup");
        }
      }
      if (execution.hasFatalError) {
        if (execution.hasCleanupAfterFatal) {
          throw new AggregateError(
            [execution.fatalError, execution.cleanupAfterFatal],
            "perf trial was cancelled and cleanup also failed",
            { cause: execution.fatalError },
          );
        }
        throw execution.fatalError;
      }

      const finished = new Date();
      const outcomes = driven?.outcomes ?? [];
      const stop = driven?.stop ?? {
        reason: "aborted" as const,
        inflight_at_stop: 0,
        interrupted: 0,
        force_cancelled: false,
      };
      const trial: TrialRecord = {
        id: arm.id,
        service: this.#experiment.service.name,
        arm,
        started_at: started.toISOString(),
        finished_at: finished.toISOString(),
        windows: buildWindows(arm.load, outcomes, driven?.stop.snapshot?.at_s ?? driven?.elapsed_s ?? 0),
        stop,
        slo: [],
        registry: {},
        probe_errors: {},
        phase_errors: execution.phaseErrors,
        outcomes,
      };
      trials.push(trial);
      await this.#experiment.onTrialFinish?.(trial);
      // A phase failure means the execution/testbed state is no longer a safe
      // baseline for the next Arm, regardless of any future SLO stop policy.
      if (trial.phase_errors.length) break;
    }
    return {
      schema: 4,
      run_id: this.#runId,
      experiment: this.#experiment.name ?? "perf",
      created_at: created.toISOString(),
      service: this.#experiment.service.name,
      passed: trials.length === arms(this.#experiment).length
        && trials.every((trial) => !trial.phase_errors.length
          && (trial.stop.reason === "deadline" || trial.stop.reason === "request_limit")),
      n_trials: trials.length,
      trials,
    };
  }
}
