import type { Outcome, RequestEvaluation } from "./model";
export type Judge = (outcome: Outcome) => RequestEvaluation;
export function validateEvaluation(value: unknown): asserts value is RequestEvaluation {
  if (value === null || typeof value !== "object" || !("code" in value) ||
      typeof value.code !== "string" || !value.code.trim())
    throw new Error("request code must be a nonempty string");
}
export function defaultJudge(outcome: Outcome): RequestEvaluation {
  const exception = outcome.meta?.exc;
  if (exception) return { code: /timeout/i.test(String(exception)) ? "timeout" : "transport_error" };
  if (outcome.status !== null && outcome.status >= 200 && outcome.status < 300)
    return { code: "success" };
  return { code: outcome.status === null ? "unknown" : `http_${outcome.status}` };
}
