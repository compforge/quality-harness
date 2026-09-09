import { httpRequests, type HttpRequest } from "../kinds/http";
import type { Finding } from "../model/node";
import type { Detector } from "./registry";

const SLOW_HTTP_MS = 200;
const MAX_FINDINGS = 10;
const duration = (request: HttpRequest) => Math.max(...request.spans.map((span) => span.dur_ms));
const label = (request: HttpRequest) => `${request.api[0]} ${request.api[1]}${request.api[2]}`;
const compareId = (a: string, b: string) => a < b ? -1 : a > b ? 1 : 0;
const wall = (run: HttpRequest[]) => run.at(-1)!.span.end_ms - run[0]!.span.start_ms;

function serialRuns(requests: HttpRequest[]): HttpRequest[][] {
  const groups = new Map<string, HttpRequest[]>();
  for (const request of requests) {
    if (!request.span.parent_span_id) continue;
    const key = JSON.stringify([request.span.service, request.span.parent_span_id]);
    const group = groups.get(key) ?? [];
    group.push(request);
    groups.set(key, group);
  }
  const runs: HttpRequest[][] = [];
  for (const siblings of groups.values()) {
    let run: HttpRequest[] = [];
    let busyUntil = -Infinity;
    for (const request of siblings.sort((a, b) => a.span.start_ms - b.span.start_ms || compareId(a.span.span_id, b.span.span_id))) {
      const serial = request.span.start_ms >= busyUntil;
      if (request.ordinary && serial && run.length && request.api.every((value, index) => value === run.at(-1)!.api[index])) {
        run.push(request);
      } else {
        if (run.length >= 2) runs.push(run);
        run = request.ordinary && serial ? [request] : [];
      }
      busyUntil = Math.max(busyUntil, request.span.end_ms);
    }
    if (run.length >= 2) runs.push(run);
  }
  return runs;
}

/** Run once per trace; use caller spans for sequence timing to avoid cross-host clock skew. */
export const httpRequestPatterns: Detector = (node, context) => {
  const anchor = context.nodes.reduce((earliest, candidate) =>
    !earliest || candidate.start_ms < earliest.start_ms
      || (candidate.start_ms === earliest.start_ms && candidate.node_id < earliest.node_id)
      ? candidate : earliest, context.nodes[0]);
  if (!anchor || node.node_id !== anchor.node_id) return [];
  const requests = httpRequests(context);
  const view = context.view();
  const findings: Finding[] = [];
  const slow = requests.filter((request) => request.ordinary && duration(request) > SLOW_HTTP_MS)
    .sort((a, b) => duration(b) - duration(a) || compareId(a.span.span_id, b.span.span_id));
  for (const request of slow.slice(0, MAX_FINDINGS)) {
    const owner = request.spans.map((span) => view.by_span.get(span.span_id)).find(Boolean) ?? node;
    findings.push({
      ref: owner.node_id,
      source: "http_slow_request",
      severity: "warn",
      symptoms: ["慢"],
      note: `普通 HTTP 请求 ${label(request)} 耗时 ${duration(request).toFixed(1)}ms > ${SLOW_HTTP_MS}ms；检查 span ${request.span.span_id} 的下游调用与请求内空档`,
      data: { method: request.api[0], target: request.api[1], route: request.api[2], duration_ms: duration(request), threshold_ms: SLOW_HTTP_MS, span_ids: request.spans.map((span) => span.span_id) },
    });
  }
  const runs = serialRuns(requests).sort((a, b) => wall(b) - wall(a));
  for (const run of runs.slice(0, MAX_FINDINGS)) {
    const first = run[0]!;
    const total = run.reduce((sum, request) => sum + request.span.dur_ms, 0);
    const gap = run.slice(1).reduce((sum, request, index) => sum + request.span.start_ms - run[index]!.span.end_ms, 0);
    const owner = view.by_span.get(first.span.parent_span_id!) ?? view.by_span.get(first.span.span_id) ?? node;
    findings.push({
      ref: owner.node_id,
      source: "http_serial_same_api",
      severity: "warn",
      symptoms: ["慢"],
      note: `${first.span.service ?? "?"} 连续串行调用 ${label(first)} ${run.length} 次，wall-clock ${wall(run).toFixed(1)}ms（请求累计 ${total.toFixed(1)}ms，间隔 ${gap.toFixed(1)}ms）；检查批量接口、请求内结果复用与 client 生命周期；同一 API 不代表参数相同；检查父 span ${first.span.parent_span_id} 的调用上下文`,
      data: { method: first.api[0], target: first.api[1], route: first.api[2], count: run.length, wall_ms: wall(run), gap_ms: gap, http_total_ms: total, parent_span_id: first.span.parent_span_id, span_ids: run.map((request) => request.span.span_id) },
    });
  }
  for (const [source, total] of [["http_slow_request", slow.length], ["http_serial_same_api", runs.length]] as const) {
    if (total > MAX_FINDINGS) findings.find((finding) => finding.source === source)!.note += `（共 ${total} 条，仅展示耗时最高的 ${MAX_FINDINGS} 条）`;
  }
  return findings;
};
