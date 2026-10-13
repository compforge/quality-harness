import { savedCallBreakdown } from "../analyze/call-breakdown";
import type { AnalysisContext } from "../analyze/context";
import { Measurements } from "../model/measurement";
import type { MeasurementFilter } from "./measurements";
import { filterMeasurements, measurementRows } from "./measurements";
import { agentRunRoots } from "./agent-run";
import { displayPayload, spanPayload } from "./payload";
import { renderDisplay } from "./engine";
import { FacetRegistry } from "./registry";
import { builtinFacets } from "./facets";
import { page, type View, type TraceView, type NodeView, type ViewRequest, type ViewRow } from "./model";

function flatten(roots: Array<Record<string, unknown>>): ViewRow[] {
  const rows: ViewRow[] = [];
  const visit = (nodes: Array<Record<string, unknown>>, depth: number, parent_id?: string) => {
    for (const node of nodes) {
      const { children, ...data } = node;
      rows.push({ ...data, parent_id, depth } as ViewRow);
      visit(children as Array<Record<string, unknown>>, depth + 1, String(node.node_id));
    }
  };
  visit(roots, 0);
  return rows;
}
export interface ViewOptions { facets?: FacetRegistry; measurementFilter?: MeasurementFilter }

/**
 * @spec View only selects and organizes prepared facts/derivations. It cannot call a Source,
 * extractor, measurer or detector. JSON, text and HTML consume this same result.
 */
export function buildView(analysis: AnalysisContext, request: Extract<ViewRequest, { scope: "trace" }>, options?: ViewOptions): TraceView;
export function buildView(analysis: AnalysisContext, request: Extract<ViewRequest, { scope: "node" }>, options?: ViewOptions): NodeView;
export function buildView(analysis: AnalysisContext, request: ViewRequest, options?: ViewOptions): View;
export function buildView(analysis: AnalysisContext, request: ViewRequest, options: ViewOptions = {}): View {
  const { trace, measurements, findings } = analysis;
  const byId = trace.tree().by_id;
  const spans = (ids: string[]) => Object.fromEntries([...new Set(ids)].filter(id => trace.spans.has(id)).map(id => [id, spanPayload(trace, id)]));
  const facets = options.facets ?? new FacetRegistry(builtinFacets());
  if (request.scope === "trace") {
    const kind = request.view ?? "default";
    if (!["default", "agent"].includes(kind)) throw new Error(`Invalid trace view: ${kind}`);
    if (kind === "agent" && analysis.agentRuns && analysis.agentRuns.trace_id !== trace.trace_id) throw new Error("AgentRun trace_id does not match trace");
    const visible = filterMeasurements(trace, measurements, options.measurementFilter);
    const roots = kind === "agent"
      ? analysis.agentRuns ? agentRunRoots(trace, analysis.agentRuns, findings) : []
      : renderDisplay(trace.tree(), findings, facets).map((node, i) => displayPayload(trace, node, byId, visible, String(i)));
    const rows = page(flatten(roots), request);
    const ids = new Set(rows.items.flatMap(row => row.source_node_ids));
    return { scope: "trace", view: kind, trace_id: trace.trace_id,
      status: kind === "agent" && !analysis.agentRuns ? "not_available" : "available", rows,
      spans: spans(rows.items.flatMap(row => row.span_ids)),
      values: Object.fromEntries(trace.nodes.filter(node => ids.has(node.node_id) && node.value).map(node => [node.node_id, node.value])),
      timings: Object.fromEntries([...ids].filter(id => visible.nodes[id]?.length).flatMap(id => { const timing = savedCallBreakdown(measurements, id); return timing ? [[id, timing]] : []; })),
      node_count: trace.nodes.length, span_count: trace.span_count,
      measurements: measurementRows(measurements, null),
      error_count: trace.nodes.filter(node => node.has_error).length };
  }
  const node = byId.get(request.nodeId);
  if (!node) throw new Error(`Trace ${trace.trace_id}: node ${request.nodeId} not found`);
  const kind = request.view ?? "default";
  if (!["default", "measurements", "timeline"].includes(kind)) throw new Error(`Invalid node view: ${kind}`);
  const detail = displayPayload(trace, { kind: node.kind, name: node.name, brief: node.brief, node_ids: [node.node_id],
    children: [], findings: [...(findings[node.node_id] ?? [])], folded: 0 }, byId, new Measurements(), node.node_id);
  const { children, ...data } = detail;
  const row = { ...data, depth: 0 } as ViewRow;
  const identity: ViewRow = { node_id: node.node_id, source_node_ids: [node.node_id], span_ids: [...node.span_ids],
    depth: 0, name: node.name, kind: node.kind, start_ms: node.start_ms, duration_ms: node.duration_ms, service: node.service };
  const base = { scope: "node" as const, trace_id: trace.trace_id, node: identity };
  if (kind === "default") return { ...base, node: row, view: kind, spans: spans(node.span_ids) };
  const prepared = savedCallBreakdown(measurements, node.node_id);
  const timing = prepared ? { status: prepared.status, error: prepared.error, window: prepared.window } : null;
  // Explicit node queries always read full measurements; the balloon is only a report selection.
  if (kind === "measurements") return { ...base, view: kind, measurements: measurementRows(measurements, node.node_id),
    timing, groups: prepared?.status === "measured" ? page([...prepared.groups].sort((a,b) => b.total - a.total || a.key.localeCompare(b.key)), request) : null };
  return { ...base, view: "timeline", timing,
    calls: prepared?.status === "measured" ? page([...prepared.calls].sort((a,b) => a.start - b.start || a.key.localeCompare(b.key)), request) : null };
}
