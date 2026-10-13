import { formatMs } from "../kinds/base";
import type { AgentRunIR } from "../model/agent";
import type { TraceContext } from "../model/context";
import type { Measurements } from "../model/measurement";
import { agentRunRoots } from "./agent-run";
import { measurementRows } from "./measurements";
import { aggregateMeasurementCalls, measurementCallDetails, measurementTimelineRows, measurementWindow, type CallGroup, type TimelineCall } from "./measurement-calls";

export interface PageOptions { offset?: number; limit?: number }
export interface InspectionPage<T> {
  items: T[];
  total: number;
  offset: number;
  next_offset: number | null;
}
function page<T>(items: T[], options: PageOptions = {}): InspectionPage<T> {
  const { offset = 0, limit = 100 } = options;
  if (!Number.isSafeInteger(offset) || offset < 0 || !Number.isSafeInteger(limit) || limit < 1) {
    throw new Error("Inspection offset must be non-negative and limit must be positive integers");
  }
  return { items: items.slice(offset, offset + limit), total: items.length, offset,
    next_offset: offset + limit < items.length ? offset + limit : null };
}
function traceStart(trace: TraceContext): number | null {
  let start = Infinity;
  for (const node of trace.nodes) start = Math.min(start, node.start_ms);
  for (const span of trace.spans.values()) start = Math.min(start, span.start_ms);
  return Number.isFinite(start) ? start : null;
}
export interface AgentStackRow {
  id: string;
  parent_id?: string;
  depth: number;
  kind: string;
  name: string;
  service: string;
  start_ms: number;
  offset_ms: number | null;
  duration_ms: number;
  source_node_ids: string[];
  span_ids: string[];
}
export interface AgentStackInspection {
  trace_id: string;
  status: "available" | "not_available";
  start_ms: number | null;
  rows: InspectionPage<AgentStackRow>;
}
/**
 * @spec Inspect prepared AgentRun IR using the same hierarchy as HTML. Display IDs are not Node IDs.
 * @why Missing AgentRun evidence must not be replaced by a guessed logical execution tree.
 */
export function inspectAgentStack(trace: TraceContext, ir?: AgentRunIR, options: PageOptions = {}): AgentStackInspection {
  if (ir && ir.trace_id !== trace.trace_id) throw new Error(`AgentRun trace ${ir.trace_id} does not match ${trace.trace_id}`);
  const start = traceStart(trace);
  const rows: AgentStackRow[] = [];
  const visit = (nodes: Array<Record<string, unknown>>, depth: number, parent?: string) => {
    for (const node of nodes) {
      const id = String(node.node_id);
      rows.push({ id, parent_id: parent, depth, kind: String(node.kind), name: String(node.name),
        service: String(node.service), start_ms: Number(node.start_ms),
        offset_ms: start === null ? null : Number(node.start_ms) - start,
        duration_ms: Number(node.duration_ms), source_node_ids: node.source_node_ids as string[], span_ids: node.span_ids as string[] });
      visit(node.children as Array<Record<string, unknown>>, depth + 1, id);
    }
  };
  if (ir) visit(agentRunRoots(trace, ir, {}, false), 0);
  return { trace_id: trace.trace_id, status: ir ? "available" : "not_available", start_ms: start, rows: page(rows, options) };
}

export interface NodeMeasurementInspection {
  trace_id: string;
  node: { node_id: string; name: string; start_ms: number; duration_ms: number; span_ids: string[] };
  measurements: ReturnType<typeof measurementRows>;
  prefix: {
    status: "measured" | "not_applicable" | "error" | "not_available";
    error: string | null;
    window: { start_ms: number; end_ms: number; elapsed_ms: number } | null;
    groups: InspectionPage<CallGroup> | null;
    calls: InspectionPage<TimelineCall> | null;
  };
}
/**
 * @spec Read all prepared measurements for the node, independent of report visibility filters.
 * Prefix calls are a projection of saved sources, never a remeasurement or a causal attribution.
 */
export function inspectNodeMeasurements(trace: TraceContext, measurements: Measurements, nodeId: string,
  options: { groups?: PageOptions; calls?: PageOptions } = {}): NodeMeasurementInspection {
  const node = trace.view().by_id.get(nodeId);
  if (!node) throw new Error(`Trace ${trace.trace_id}: node ${nodeId} not found`);
  const prefix = measurements.get(nodeId, "calls_until_node_end");
  const result: NodeMeasurementInspection = {
    trace_id: trace.trace_id,
    node: { node_id: nodeId, name: node.name, start_ms: node.start_ms, duration_ms: node.duration_ms, span_ids: [...node.span_ids] },
    measurements: measurementRows(measurements, nodeId),
    prefix: { status: prefix?.status ?? "not_available", error: prefix?.error ?? null, window: null, groups: null, calls: null },
  };
  if (prefix?.status !== "measured") return result;
  const { start_ms: start, end_ms: end, source } = prefix.evidence;
  if (source !== "calls" || typeof start !== "number" || typeof end !== "number") {
    throw new Error(`Trace ${trace.trace_id}: node ${nodeId} has invalid prefix evidence`);
  }
  const calls = measurementWindow(measurementCallDetails(trace, measurements), start, end);
  result.prefix.window = { start_ms: start, end_ms: end, elapsed_ms: end - start };
  result.prefix.groups = page(aggregateMeasurementCalls(calls), options.groups);
  result.prefix.calls = page(measurementTimelineRows(calls), options.calls);
  return result;
}

const text = (value: string) => JSON.stringify(value);
function pageLabel<T>(value: InspectionPage<T>): string {
  return `${value.items.length}/${value.total} rows; offset=${value.offset}; next_offset=${value.next_offset ?? "none"}`;
}
export function renderAgentStackText(result: AgentStackInspection): string {
  return [
    `trace=${result.trace_id} Agent stack: ${result.status}; origin_ms=${result.start_ms ?? "unknown"}`,
    pageLabel(result.rows),
    ...result.rows.items.map(row => `${"  ".repeat(row.depth)}${row.kind} ${text(row.name)} service=${text(row.service)} +${row.offset_ms === null ? "unknown" : formatMs(row.offset_ms)} duration=${formatMs(row.duration_ms)} id=${text(row.id)} parent=${text(row.parent_id ?? "")} nodes=${JSON.stringify(row.source_node_ids)} spans=${JSON.stringify(row.span_ids)}`),
  ].join("\n") + "\n";
}
export function renderNodeMeasurementsText(result: NodeMeasurementInspection): string {
  const lines = [
    `trace=${result.trace_id} node=${text(result.node.node_id)} ${text(result.node.name)} duration=${formatMs(result.node.duration_ms)}`,
    "Measurements (saved values; missing is not zero):",
    ...result.measurements.map(row => JSON.stringify(row)),
    `Prefix: ${result.prefix.status}${result.prefix.error ? ` ${text(result.prefix.error)}` : ""}`,
  ];
  const { window, groups, calls } = result.prefix;
  if (window && groups && calls) {
    lines.push(`Earliest observed trace start → node end: ${formatMs(window.elapsed_ms)} wall-clock (${window.start_ms}..${window.end_ms} ms)`,
      "Nested/parallel calls and kinds overlap. Sums are not causal wall-clock contributions.",
      `Call groups: ${pageLabel(groups)} (durations in ms)`);
    for (const group of groups.items) lines.push(`${group.kind} ${text(group.service)} ${text(group.target)} ${text(group.name)} count=${group.count} sum_ms=${group.total} avg_ms=${group.average} max_ms=${group.max} key=${text(group.key)}`);
    lines.push(`Timeline: ${pageLabel(calls)} (offsets relative to window start)`);
    for (const call of calls.items) lines.push(`${"  ".repeat(call.depth)}${call.kind} ${text(call.service)} ${text(call.name)} +${formatMs(call.start)}..+${formatMs(call.end)} duration=${formatMs(call.duration)} clipped=${call.clipped} key=${text(call.key)} parent=${text(call.parent ?? "")} node=${text(call.node_id)} spans=${JSON.stringify(call.span_ids)}`);
  }
  return lines.join("\n") + "\n";
}
