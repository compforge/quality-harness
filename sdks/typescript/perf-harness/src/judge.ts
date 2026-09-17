import type { Outcome, RequestEvaluation } from "./model";
export type Judge = (outcome: Outcome) => RequestEvaluation;
export function defaultJudge(outcome: Outcome): RequestEvaluation {
  const exception = outcome.meta?.exc;
  if (exception) return { ok: false, error_kind: String(exception) };
  if (outcome.status !== null && outcome.status >= 200 && outcome.status < 300)
    return { ok: true };
  return {
    ok: false,
    error_kind: outcome.status === null ? "unknown" : String(outcome.status),
  };
}
