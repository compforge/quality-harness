import type { Dependency } from "../loading/facts";
import { httpRequests } from "../kinds/http";
import type { TraceContext } from "../model/context";
import { intervalUnion } from "../model/intervals";
import { Measurements, type Measurement, type MeasurementSpec, type CallSource } from "../model/measurement";

export interface Measurer {
  spec: MeasurementSpec;
  requires?(trace: TraceContext): readonly Dependency[];
  compute(trace: TraceContext, sources: readonly CallSource[]): Iterable<Measurement>;
}
export const CALLS: MeasurementSpec = {
  id: "calls_until_node_end", scope: "trace_prefix",
  units: { count: "call", duration_sum_ms: "ms", covered_ms: "ms" }, dimensions: ["kind"],
  description: "Calls started from earliest observed trace start through anchor end; in-flight durations clipped. Kind coverage can overlap.",
};
export const SELF: MeasurementSpec = {
  id: "self_ms", scope: "node", units: { self_ms: "ms" }, dimensions: [],
  description: "Node duration not covered by direct children, clipped to the node interval.",
};

export function callSources(trace: TraceContext): CallSource[] {
  // Pairing remains protocol-owned. Counting deliberately includes model HTTP and SSE.
  const requests = httpRequests(trace);
  const claimed = new Set(requests.flatMap((request) => request.spans.map((span) => span.span_id)));
  const sources: CallSource[] = requests.map((request) => ({
    id: request.span.span_id, kind: "http", start_ms: request.span.start_ms, end_ms: request.span.end_ms,
    node_id: trace.view().by_span.get(request.span.span_id)!.node_id,
    span_ids: request.spans.map((span) => span.span_id),
  }));
  for (const node of trace.nodes) {
    if (node.kind === "service" || (node.kind === "http" && node.span_ids.some((id) => claimed.has(id)))) continue;
    sources.push({ id: node.node_id, kind: node.kind, start_ms: node.start_ms, end_ms: node.end_ms, node_id: node.node_id, span_ids: [...node.span_ids] });
  }
  return sources.sort((a, b) => a.start_ms - b.start_ms || a.kind.localeCompare(b.kind) || a.id.localeCompare(b.id));
}
const rounded = (value: number): number => Math.round(value * 1000) / 1000;
type Counts = Record<string, { count: number; duration_sum_ms: number; covered_ms: number }>;

/** One sweep per kind, including starts at each cutoff. Evidence stays in a shared index. */
export function prefixValues(sources: readonly CallSource[], cutoffs: Iterable<number>, start: number): Map<number, Counts> {
  const cuts = [...new Set(cutoffs)].sort((a, b) => a - b);
  if (!Number.isFinite(start) || cuts.some((cut) => !Number.isFinite(cut)) || sources.some((source) => !Number.isFinite(source.start_ms) || !Number.isFinite(source.end_ms) || source.end_ms < source.start_ms)) throw new Error("invalid measurement interval");
  const output = new Map(cuts.map((cut) => [cut, {} as Counts]));
  const groups = new Map<string, CallSource[]>();
  for (const source of sources) {
    const group = groups.get(source.kind) ?? []; group.push(source); groups.set(source.kind, group);
  }
  for (const kind of [...groups.keys()].sort()) {
    const events = new Map<number, [number, number]>();
    for (const source of groups.get(kind)!) {
      const begin = events.get(source.start_ms) ?? [0, 0]; begin[0]++; events.set(source.start_ms, begin);
      const end = events.get(source.end_ms) ?? [0, 0]; end[1]++; events.set(source.end_ms, end);
    }
    const times = [...events.keys()].sort((a, b) => a - b);
    let index = 0, active = 0, count = 0, total = 0, covered = 0, previous = start;
    for (const cut of cuts) {
      while (index < times.length && times[index]! <= cut) {
        const time = times[index]!;
        const elapsed = Math.max(0, time - previous);
        total += elapsed * active; covered += active ? elapsed : 0;
        const [starts, ends] = events.get(time)!;
        count += starts; active += starts - ends; previous = time; index++;
      }
      const elapsed = Math.max(0, cut - previous);
      output.get(cut)![kind] = { count, duration_sum_ms: rounded(total + elapsed * active), covered_ms: rounded(covered + (active ? elapsed : 0)) };
    }
  }
  return output;
}
function result(spec: MeasurementSpec, nodeId: string, values: Record<string, unknown>, evidence: Record<string, unknown>): Measurement {
  return { spec_id: spec.id, anchor_node_id: nodeId, status: "measured", values, evidence, error: null };
}
export function builtinMeasurers(): Measurer[] {
  return [{ spec: SELF, compute: (trace) => trace.nodes.map((node) => {
    if (!Number.isFinite(node.start_ms) || !Number.isFinite(node.duration_ms) || node.duration_ms < 0) throw new Error("invalid measurement interval");
    const intervals = trace.view().children(node).map((child): [number, number] => [Math.max(node.start_ms, child.start_ms), Math.min(node.end_ms, child.end_ms)]).filter(([s, e]) => e > s);
    return result(SELF, node.node_id, { self_ms: rounded(Math.max(0, node.duration_ms - intervalUnion(intervals))) }, { source: "direct_children", node_id: node.node_id });
  }) }, { spec: CALLS, compute: (trace, sources) => {
    const starts = [...trace.spans.values()].map((span) => span.start_ms).concat(trace.nodes.map((node) => node.start_ms));
    const start = starts.length ? starts.reduce((a, b) => Math.min(a, b)) : 0;
    const values = prefixValues(sources, trace.nodes.map((node) => node.end_ms), start);
    return trace.nodes.map((node) => result(CALLS, node.node_id, values.get(node.end_ms)!, { source: "calls", start_ms: start, end_ms: node.end_ms }));
  } }];
}
export function measure(trace: TraceContext, measurers: Iterable<Measurer> = builtinMeasurers()): Measurements {
  const items = [...measurers];
  if (new Set(items.map((item) => item.spec.id)).size !== items.length) throw new Error("duplicate measurement id");
  const sources = callSources(trace);
  const output = new Measurements(items.map((item) => item.spec), sources);
  for (const item of items) {
    let results: Map<string, Measurement>;
    try {
      const values = [...item.compute(trace, sources)];
      results = new Map(values.map((value) => [value.anchor_node_id, value]));
      if (results.size !== values.length || values.some((value) => value.spec_id !== item.spec.id || !["measured", "not_applicable", "error"].includes(value.status) || (value.status !== "measured" && Object.keys(value.values).length > 0) || !trace.view().by_id.has(value.anchor_node_id))) throw new Error("invalid measurer output");
    } catch (error) {
      results = new Map(trace.nodes.map((node) => [node.node_id, { spec_id: item.spec.id, anchor_node_id: node.node_id, status: "error", values: {}, evidence: {}, error: error instanceof Error ? error.message : String(error) }]));
    }
    for (const node of trace.nodes) (output.results[node.node_id] ??= []).push(results.get(node.node_id) ?? { spec_id: item.spec.id, anchor_node_id: node.node_id, status: "not_applicable", values: {}, evidence: {}, error: null });
  }
  return output;
}
