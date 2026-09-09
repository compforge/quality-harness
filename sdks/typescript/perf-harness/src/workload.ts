import type { Case } from "@compforge/spec-case/model";
import type { Arm, Outcome, Service, Verdict } from "./model";

export interface TrialContext {
  service: Service;
  arm: Arm;
  run_id: string;
  signal: AbortSignal;
}

export interface FireContext extends TrialContext {
  case: Case;
}

export interface Workload {
  setup?(context: TrialContext): Promise<void>;
  fire(context: FireContext): Promise<Outcome>;
  judge?(outcome: Outcome): Verdict;
  deactivate?(context: TrialContext): Promise<void>;
  cleanup?(context: TrialContext): Promise<void>;
}

export function defaultJudge(outcome: Outcome): Verdict {
  const exception = outcome.meta?.exc;
  if (exception) return { ok: false, error_kind: String(exception) };
  if (outcome.status !== null && outcome.status >= 200 && outcome.status < 300) return { ok: true };
  return { ok: false, error_kind: outcome.status === null ? "unknown" : String(outcome.status) };
}
