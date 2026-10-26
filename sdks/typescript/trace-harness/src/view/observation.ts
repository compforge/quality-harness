import type { Node as ObservationNode, SessionView, Input, ModelCall, ToolCall, Operation, Turn } from "@compforge/agent-observation";
import type { TraceContext } from "../model/context";
import type { Finding, Node } from "../model/node";
import { sourceNodeIds, validateSessionViews } from "../observation";
import { DisplayName, nameProjections } from "./display";
import { toolNameDetail } from "./tool-name";

function text(value: unknown): string {
  return typeof value === "string" ? value : JSON.stringify(value, null, 2);
}

function sourcePayload(
  context: TraceContext,
  sourceNodeIds: string[] = [],
  findings: Readonly<Record<string, readonly Finding[]>>,
  status = "",
): Record<string, unknown> {
  const nodes = sourceNodeIds
    .map((nodeId) => context.tree().by_id.get(nodeId))
    .filter((node): node is Node => Boolean(node));
  const spanIds = [...new Set(nodes.flatMap((node) => node.span_ids))];
  const errorSpanIds = [...new Set(nodes.flatMap((node) => node.error_span_ids))];
  const statusError = ["error", "failed", "failure"].includes(status.toLowerCase());
  return {
    source_node_ids: sourceNodeIds,
    service: nodes[0]?.service ?? "",
    has_error: errorSpanIds.length > 0 || statusError,
    error: errorSpanIds.length ? context.error_text(errorSpanIds[0]!) || nodes.find(node => node.error_text)?.error_text || status : status,
    findings: nodes.flatMap((node) => (findings[node.node_id] ?? []).map((finding) => ({
      severity: finding.severity,
      source: finding.source,
      note: finding.note ?? "",
    }))),
    span_ids: spanIds,
    primary_span_id: nodes[0]?.primary_span_id ?? "",
    error_span_ids: errorSpanIds,
  };
}

function facts(
  status = "",
  attributes: Record<string, unknown> = {},
  sourceNodeIds: string[] = [],
): Record<string, string> {
  const values = Object.fromEntries(Object.entries(attributes).map(([key, value]) => [key, text(value)]));
  if (status) values.status = status;
  if (sourceNodeIds.length) values.source_nodes = sourceNodeIds.join(", ");
  return values;
}

function payloadHasError(payload: Record<string, unknown>): boolean {
  return Boolean(payload.has_error) || (payload.children as Array<Record<string, unknown>>)
    .some(payloadHasError);
}


function sources(item: ObservationNode, context: TraceContext): string[] {
  const own = sourceNodeIds(item, context);
  if (own.length || (item.kind !== "turn" && item.kind !== "operation")) return own;
  return [...new Set(((item as Operation | Turn).nodes ?? []).flatMap(child => sources(child, context)))];
}

function itemPayload(context: TraceContext, observed: ObservationNode, identity: string[], findings: Readonly<Record<string, readonly Finding[]>>, catalog: Map<string, SessionView>, active: Set<string>): Record<string, unknown> {
  const item = observed as Input | ModelCall | ToolCall | Operation | Turn;
  const refs = sources(item, context);
  const itemFacts = facts(item.status, item.attributes, refs);
  if (item.started_at) itemFacts.started_at = item.started_at;
  if (item.finished_at) itemFacts.finished_at = item.finished_at;
  itemFacts.evidence = text(item.evidence ?? []);
  if (item.run_id) itemFacts.run_id = item.run_id;
  if (item.lane_id) itemFacts.lane_id = item.lane_id;
  const details: Record<string, string> = {};
  let detail = "", brief = "";
  if (item.kind === "model_call" || item.kind === "tool_call") {
    if (item.request) details.request = text(item.request);
    if (item.response) details.response = text(item.response);
    if (item.kind === "tool_call") {
      detail = toolNameDetail(item.request?.input);
      if (item.tool_call_id) itemFacts.tool_call_id = item.tool_call_id;
      if (item.model_call_id) itemFacts.model_call_id = item.model_call_id;
    } else if (item.request?.model) {
      itemFacts.requested_model = text(item.request.model);
      brief = `model=${item.request.model.id}`;
    }
  } else if (item.kind === "input") {
    details.content = text(item.content);
    if (item.mode) itemFacts.mode = item.mode;
  } else if (item.kind === "operation") {
    itemFacts.operation = item.operation;
    details.data = text(item.data ?? null);
  }
  const name = item.name || (item.kind === "tool_call" ? item.request?.tool_name : undefined)
    || (item.kind === "operation" ? item.operation : item.kind);
  const children = item.kind === "turn" || item.kind === "operation"
    ? (item.nodes ?? []).map(child => itemPayload(context, child, identity, findings, catalog, active)) : [];
  if (item.kind === "tool_call" && item.sessions?.length) {
    const refs = new Map(item.sessions.map(ref => [JSON.stringify([ref.source, ref.id]), ref]));
    details.sessions = text([...refs.values()].map(ref => {
      const key = JSON.stringify([ref.source, ref.id]);
      const target = catalog.get(key);
      const state = active.has(key) ? "cycle" : target ? "available" : "unavailable";
      if (state === "available" && target) children.push(sessionPayload(context, target, findings, catalog, active, [...identity, item.id]));
      return { ...ref, state };
    }));
  }
  const source = sourcePayload(context, refs, findings, item.status);
  const start = item.started_at ? Date.parse(item.started_at) : undefined;
  const end = item.finished_at ? Date.parse(item.finished_at) : undefined;
  return {
    node_id: `observation:${JSON.stringify([...identity, item.id])}`, kind: item.kind, name,
    name_variants: nameProjections(new DisplayName(name, detail)),
    // Layout fallback only; unavailable timing is not written back to observations.
    timing_known: start !== undefined && end !== undefined,
    start_ms: start ?? 0, duration_ms: start !== undefined && end !== undefined ? end - start : 0,
    brief, facts: itemFacts, details, folded: 0,
    collapsed: item.kind === "operation" && children.length > 0 && !source.has_error && !children.some(payloadHasError),
    children, ...source,
  };
}

function sessionPayload(context: TraceContext, view: SessionView, findings: Readonly<Record<string, readonly Finding[]>>, catalog: Map<string, SessionView>, active: Set<string>, path: string[]): Record<string, unknown> {
  const key = JSON.stringify([view.session.source, view.session.id]);
  const identity = [...path, view.session.source, view.session.id];
  const children = view.nodes.map(item => itemPayload(context, item, identity, findings, catalog, new Set([...active, key])));
  const refs = [...new Set(view.nodes.flatMap(item => sources(item, context)))];
  return {
    node_id: `session:${JSON.stringify(identity)}`, kind: "session", name: view.session.id, name_variants: [view.session.id],
    timing_known: false, start_ms: 0, duration_ms: 0, brief: view.session.source,
    facts: facts("", view.attributes, refs), details: { issues: text(view.issues ?? []), lanes: text(view.lanes ?? []) },
    folded: 0, children, ...sourcePayload(context, refs, findings),
  };
}

/** @spec Expand supplied Session references at call sites; cycles and absent views stay references. */
export function sessionRoots(context: TraceContext, views: readonly SessionView[], findings: Readonly<Record<string, readonly Finding[]>>): Array<Record<string, unknown>> {
  const checked = validateSessionViews(views, context);
  const catalog = new Map(checked.map(view => [JSON.stringify([view.session.source, view.session.id]), view]));
  return checked.map(view => sessionPayload(context, view, findings, catalog, new Set(), []));
}
