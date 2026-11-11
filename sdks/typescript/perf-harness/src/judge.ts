import type { Outcome, RequestEvaluation } from "./model";
export type Judge = (outcome: Outcome) => RequestEvaluation;
export function defaultJudge(outcome: Outcome): RequestEvaluation {
  const exception = outcome.meta?.exc;
  if (exception) return { result: /timeout/i.test(String(exception)) ? "timeout" : "failure", error_kind: String(exception) };
  if (outcome.status !== null && outcome.status >= 200 && outcome.status < 300)
    return { result: "success" };
  // 503 alone does not establish rejection; only 429 explicitly refuses admission.
  return {
    result: outcome.status === 429 ? "rejected" : outcome.status === 408 || outcome.status === 504 ? "timeout" : "failure",
    error_kind: outcome.status === null ? "unknown" : String(outcome.status),
  };
}
