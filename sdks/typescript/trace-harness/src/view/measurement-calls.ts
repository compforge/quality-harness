import { httpEndpoint } from "../kinds/http";
import type { TraceContext } from "../model/context";
import type { Measurements } from "../model/measurement";

/**
 * @spec Report projections retain the measured call identities and their original intervals.
 * @why Aggregation and window clipping are view operations; neither changes analysis or HTTP pairing.
 */
export function measurementCallDetails(trace: TraceContext, measurements: Measurements) {
  const nodes = trace.view().by_id;
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
export function measurementTimelineRows(calls: readonly WindowedCall[], collapsed: ReadonlySet<string> = new Set(), filter: string | null = null): TimelineCall[] {
  const byKey = new Map(calls.map(call => [call.key, call]));
  const children = new Map<string | null, WindowedCall[]>();
  for (const call of calls) {
    const parent = call.parent && byKey.has(call.parent) ? call.parent : null;
    const list = children.get(parent) ?? []; list.push(call); children.set(parent, list);
  }
  for (const list of children.values()) list.sort((a,b) => a.start - b.start || b.end - a.end || a.key.localeCompare(b.key));
  const visible = new Set<string>();
  if (filter) for (const call of calls) if (call.group === filter) {
    for (let item: WindowedCall | undefined = call; item && !visible.has(item.key); item = item.parent ? byKey.get(item.parent) : undefined) visible.add(item.key);
  }
  const rows: TimelineCall[] = [];
  const visit = (parent: string | null, depth: number) => {
    for (const call of children.get(parent) ?? []) {
      if (filter && !visible.has(call.key)) continue;
      rows.push({ ...call, depth, hasChildren: (children.get(call.key) ?? []).length > 0 });
      if (filter || !collapsed.has(call.key)) visit(call.key, depth + 1);
    }
  };
  visit(null, 0);
  return rows;
}
