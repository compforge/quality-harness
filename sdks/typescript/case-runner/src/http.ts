import { validateHttpCase, type HttpCase, type HttpExpectation } from "@compforge/spec-case/http";

/** Runtime-only access data; never part of canonical Case serialization or intent hash. */
export interface HttpTarget {
  readonly url: string;
  readonly headers?: Readonly<Record<string, string>>;
  readonly alternatives?: readonly { readonly id: string; readonly url: string }[];
}
export interface PreparedHttpCase {
  readonly case: HttpCase;
  readonly target: HttpTarget;
}
export interface HttpBudget { readonly timeoutMs: number; readonly maxResponseBytes: number }
export interface HttpCaseRequest extends HttpBudget {
  readonly method: string;
  readonly url: string;
  readonly headers: Record<string, string>;
  readonly body?: Uint8Array;
  readonly followRedirects: false;
}
export interface HttpObservation {
  readonly response: {
    readonly statusCode?: number;
    readonly contentType?: string;
    readonly captureComplete: boolean;
    readonly error?: string;
  };
}
export type HttpMismatch = "transport" | "status" | "content-type";
export interface HttpJudgment {
  readonly status: "passed" | "failed" | "observed";
  readonly mismatches: readonly HttpMismatch[];
}

function validateUrl(value: string): void {
  const url = new URL(value);
  if (!["http:", "https:"].includes(url.protocol) || url.username || url.password) throw new Error("HTTP target must be a credential-free HTTP(S) URL");
}

export function validatePreparedHttpCase(value: PreparedHttpCase): void {
  validateHttpCase(value.case);
  validateUrl(value.target.url);
  if (value.target.headers !== undefined && (!value.target.headers || typeof value.target.headers !== "object" || Array.isArray(value.target.headers))) throw new Error("Invalid HTTP target headers");
  for (const [key, header] of Object.entries(value.target.headers ?? {})) {
    if (!/^[!#$%&'*+.^_`|~0-9A-Za-z-]+$/.test(key) || typeof header !== "string" || /[\r\n]/.test(header)) throw new Error("Invalid HTTP target header");
  }
  const ids = new Set(["primary"]);
  if (value.target.alternatives !== undefined && !Array.isArray(value.target.alternatives)) throw new Error("HTTP alternatives must be an array");
  for (const entry of value.target.alternatives ?? []) {
    if (typeof entry.id !== "string" || !entry.id.trim() || ids.has(entry.id)) throw new Error("Empty or duplicate HTTP entrypoint id");
    ids.add(entry.id);
    validateUrl(entry.url);
  }
}

/** Pure response assessment; incomplete capture cannot establish success even after HTTP 200. */
export function judgeHttpResponse(observation: HttpObservation, expect?: HttpExpectation): HttpJudgment {
  const response = observation.response;
  const mismatches: HttpMismatch[] = [];
  if (!response.captureComplete || response.error) mismatches.push("transport");
  if (expect) {
    if (response.statusCode === undefined || !expect.status.includes(response.statusCode)) mismatches.push("status");
    if (expect.contentType && response.contentType?.split(";", 1)[0]?.trim().toLowerCase() !== expect.contentType.trim().toLowerCase()) mismatches.push("content-type");
  }
  return { status: mismatches.length ? "failed" : expect ? "passed" : "observed", mismatches };
}

/** @spec Execution location belongs to the injected channel; this runner never creates a Host fallback. */
export async function executeHttpCase<O extends HttpObservation>(prepared: PreparedHttpCase,
  entrypoint: string, budget: HttpBudget, signal: AbortSignal,
  execute: (request: HttpCaseRequest, signal: AbortSignal) => Promise<O>,
): Promise<{ request: HttpCaseRequest; observation: O; judgment: HttpJudgment }> {
  validatePreparedHttpCase(prepared);
  for (const value of [budget.timeoutMs, budget.maxResponseBytes]) {
    if (!Number.isSafeInteger(value) || value <= 0) throw new Error("HTTP execution requires positive timeout and response byte budgets");
  }
  const input = prepared.case.input;
  const targetUrl = entrypoint === "primary" ? prepared.target.url : prepared.target.alternatives?.find(entry => entry.id === entrypoint)?.url;
  if (!targetUrl) throw new Error("Unknown HTTP entrypoint");
  const headers = Object.fromEntries(Object.entries(input.headers ?? {}).map(([name, value]) => [name.toLowerCase(), value]));
  // Runtime authentication overrides stable headers case-insensitively, matching HTTP semantics.
  for (const [name, value] of Object.entries(prepared.target.headers ?? {})) headers[name.toLowerCase()] = value;
  const request: HttpCaseRequest = { ...budget, method: input.method,
    url: input.path === undefined ? targetUrl : new URL(input.path, targetUrl).toString(), headers,
    ...(input.body === undefined ? {} : { body: new TextEncoder().encode(input.body) }), followRedirects: false };
  signal.throwIfAborted();
  const observation = await execute(request, signal);
  // Retain a completed/interrupted observation; the lifecycle owner records cancellation with its evidence.
  return { request, observation, judgment: judgeHttpResponse(observation, prepared.case.judge?.e2e?.http) };
}
