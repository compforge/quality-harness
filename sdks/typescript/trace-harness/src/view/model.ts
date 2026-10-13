import type { CallBreakdown, CallGroup, WindowedCall } from "../analyze/call-breakdown";

export type TraceViewKind = "default" | "agent";
export type NodeViewKind = "default" | "measurements" | "timeline";
export type ViewRequest =
  | { scope: "trace"; view?: TraceViewKind; offset?: number; limit?: number }
  | { scope: "node"; nodeId: string; view?: NodeViewKind; offset?: number; limit?: number };
export interface ViewPage<T> { items: T[]; total: number; offset: number; next_offset: number | null }

/** Format-neutral display row. A synthetic row retains its original source references. */
export interface ViewRow {
  node_id: string;
  parent_id?: string;
  depth: number;
  source_node_ids: string[];
  span_ids: string[];
  name: string;
  kind: string;
  start_ms: number;
  duration_ms: number;
  service?: string;
  [key: string]: unknown;
}
interface ViewBase { trace_id: string }
export interface TraceView extends ViewBase {
  scope: "trace";
  view: TraceViewKind;
  status: "available" | "not_available";
  rows: ViewPage<ViewRow>;
  spans: Record<string, Record<string, unknown>>;
  values: Record<string, string>;
  timings: Record<string, CallBreakdown>;
  node_count: number;
  span_count: number;
  error_count: number;
  measurements: Array<Record<string, unknown>>;
}
interface NodeViewBase extends ViewBase { scope: "node"; node: ViewRow }
export interface NodeDefaultView extends NodeViewBase {
  view: "default";
  spans: Record<string, Record<string, unknown>>;
}
export interface NodeMeasurementsView extends NodeViewBase {
  view: "measurements";
  measurements: Array<Record<string, unknown>>;
  timing: Pick<CallBreakdown, "status" | "error" | "window"> | null;
  groups: ViewPage<CallGroup> | null;
}
export interface NodeTimelineView extends NodeViewBase {
  view: "timeline";
  timing: Pick<CallBreakdown, "status" | "error" | "window"> | null;
  calls: ViewPage<WindowedCall> | null;
}
export type NodeView = NodeDefaultView | NodeMeasurementsView | NodeTimelineView;
export type View = TraceView | NodeView;

export function page<T>(items: T[], options: { offset?: number; limit?: number }): ViewPage<T> {
  const { offset = 0, limit = Math.max(1, items.length) } = options;
  if (!Number.isSafeInteger(offset) || offset < 0 || !Number.isSafeInteger(limit) || limit < 1) throw new Error("View offset must be non-negative and limit must be positive integers");
  return { items: items.slice(offset, offset + limit), total: items.length, offset,
    next_offset: offset + limit < items.length ? offset + limit : null };
}
