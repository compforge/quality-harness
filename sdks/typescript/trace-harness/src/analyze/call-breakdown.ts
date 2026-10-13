import { httpEndpoint } from "../kinds/http";
import type { TraceContext } from "../model/context";
import type { Measurement, MeasurementSpec, Measurements } from "../model/measurement";

/**
 * @spec Report projections retain the measured call identities and their original intervals.
 * @why Window clipping and operation aggregation derive node timing data from existing call sources; HTTP pairing is preserved.
 */
export function measurementCallDetails(trace: TraceContext, measurements: Measurements) {
  const nodes = trace.tree().by_id;
  const nodeCalls = new Map(measurements.sources.filter(call => call.kind !== "http").map(call => [call.node_id, `${call.kind}:${call.id}`]));
  return measurements.sources.map(call => {
    const node = nodes.get(call.node_id);
    const span = trace.spans.get(call.kind === "http" ? call.span_ids[0] ?? "" : node?.primary_span_id ?? "");
    const endpoint = span && (call.kind === "http" || call.kind === "service-api") ? httpEndpoint(span) : undefined;
    const serverRoute = call.kind === "http" ? call.span_ids.slice(1).map(id => trace.spans.get(id)).filter(item => item !== undefined).map(item => httpEndpoint(item!)).find(item => item !== undefined)?.[2] : undefined;
    const name = endpoint ? `${endpoint[0]} ${serverRoute ?? endpoint[2]}` : node?.name ?? span?.name ?? call.id;
    let parent = node?.parent_node_id;
    while (parent && !nodeCalls.has(parent)) parent = nodes.get(parent)?.parent_node_id;
    return {
      ...call, key: `${call.kind}:${call.id}`,
      parent: call.kind === "http" && nodeCalls.has(call.node_id) ? nodeCalls.get(call.node_id) : parent ? nodeCalls.get(parent) : undefined,
      name, target: endpoint?.[1] ?? "",
      service: (call.kind === "http" ? span?.service : node?.service) ?? node?.service ?? "",
    };
  });
}

export type MeasurementCall = ReturnType<typeof measurementCallDetails>[number];
export type WindowedCall = MeasurementCall & {
  start: number;
  end: number;
  duration: number;
  clipped: boolean;
  group: string;
};
export interface CallGroup {
  key: string;
  kind: string;
  service: string;
  target: string;
  name: string;
  count: number;
  total: number;
  max: number;
  average: number;
}
export type TimelineCall = WindowedCall & { depth: number; hasChildren: boolean };

/** @spec Window offsets and durations are milliseconds; inclusive cutoff retains zero-time calls. */
export function measurementWindow(calls: readonly MeasurementCall[], start: number, end: number): WindowedCall[] {
  if (!Number.isFinite(start) || !Number.isFinite(end) || end < start) throw new Error("Invalid measurement window");
  return calls.filter(call => call.start_ms <= end && call.end_ms >= start).map(call => ({
    ...call, start: Math.max(start, call.start_ms) - start,
    end: Math.min(end, call.end_ms) - start,
    duration: Math.max(0, Math.min(end, call.end_ms) - Math.max(start, call.start_ms)),
    clipped: call.start_ms < start || call.end_ms > end,
    group: JSON.stringify([call.kind, call.service, call.target || "", call.name]),
  }));
}
export function aggregateMeasurementCalls(calls: readonly WindowedCall[]): CallGroup[] {
  const groups = new Map<string, CallGroup>();
  for (const call of calls) {
    let group = groups.get(call.group);
    if (!group) {
      group = { key: call.group, kind: call.kind, service: call.service, target: call.target, name: call.name, count: 0, total: 0, max: 0, average: 0 };
      groups.set(call.group, group);
    }
    group.count++; group.total += call.duration; group.max = Math.max(group.max, call.duration);
    group.average = group.total / group.count;
  }
  return [...groups.values()].sort((a, b) => b.total - a.total || a.key.localeCompare(b.key));
}

export interface CallBreakdown {
  status: "measured" | "not_applicable" | "error" | "not_available";
  error: string | null;
  window: { start_ms: number; end_ms: number; elapsed_ms: number } | null;
  calls: WindowedCall[];
  groups: CallGroup[];
}

/** @spec Derive timing once on explicit preparation, independently of rendering or visibility. */
export function deriveCallBreakdown(trace: TraceContext, measurements: Measurements, nodeId: string): CallBreakdown {
  if (!trace.tree().by_id.has(nodeId)) throw new Error(`Trace ${trace.trace_id}: node ${nodeId} not found`);
  const prefix = measurements.get(nodeId, "calls_until_node_end");
  const result: CallBreakdown = { status: prefix?.status ?? "not_available", error: prefix?.error ?? null,
    window: null, calls: [], groups: [] };
  if (prefix?.status !== "measured") return result;
  const { start_ms: start, end_ms: end, source } = prefix.evidence;
  if (source !== "calls" || typeof start !== "number" || typeof end !== "number") {
    throw new Error(`Trace ${trace.trace_id}: node ${nodeId} has invalid prefix evidence`);
  }
  result.calls = measurementWindow(measurementCallDetails(trace, measurements), start, end);
  result.groups = aggregateMeasurementCalls(result.calls);
  result.window = { start_ms: start, end_ms: end, elapsed_ms: end - start };
  return result;
}

/** @spec This explicit preparation stores descriptive results; it never emits Findings. */
export const CALL_BREAKDOWN: MeasurementSpec = { kind: "call_breakdown", scope: "node", dimensions: [],
  units: { window: "ms", calls: "ms", groups: "ms" }, description: "Clipped calls and operation aggregates through this node's end." };
export function prepareCallBreakdown(trace: TraceContext, measurements: Measurements, nodeId: string): void {
  if (measurements.get(nodeId, CALL_BREAKDOWN.kind)) return;
  const data = deriveCallBreakdown(trace, measurements, nodeId);
  if (data.status === "not_available") return;
  if (!measurements.specs.some(spec => spec.kind === CALL_BREAKDOWN.kind)) measurements.specs.push(CALL_BREAKDOWN);
  const value: Measurement = { kind: CALL_BREAKDOWN.kind, node_id: nodeId, status: data.status, error: data.error,
    values: data.status === "measured" ? { window: data.window, calls: data.calls, groups: data.groups } : {},
    evidence: { measurement: "calls_until_node_end", node_id: nodeId } };
  (measurements.nodes[nodeId] ??= []).push(value);
}
export function savedCallBreakdown(measurements: Measurements, nodeId: string): CallBreakdown | undefined {
  const saved = measurements.get(nodeId, CALL_BREAKDOWN.kind);
  return saved ? { status: saved.status, error: saved.error,
    window: saved.values.window as CallBreakdown["window"] ?? null,
    calls: saved.values.calls as WindowedCall[] ?? [], groups: saved.values.groups as CallGroup[] ?? [] } : undefined;
}
