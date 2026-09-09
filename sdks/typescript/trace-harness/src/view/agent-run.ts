import type { AgentRun, AgentRunIR, AgentTurn, TurnItem } from "../model/agent";
import type { TraceContext } from "../model/context";
import type { Finding, Node } from "../model/node";
import { DisplayName, nameProjections, type Compact } from "./display";
import { toolNameDetail } from "./tool-name";

function text(value: unknown): string {
  return typeof value === "string" ? value : JSON.stringify(value, null, 2);
}

function compactNames(component: unknown, name: string, detail = ""): string[] {
  const compact = component && typeof (component as Partial<Compact>).compact === "function"
    ? component as Compact
    : new DisplayName(name, detail);
  return nameProjections(compact);
}

function sourcePayload(
  context: TraceContext,
  sourceNodeIds: string[] = [],
  findings: Record<string, Finding[]>,
  status = "",
): Record<string, unknown> {
  const nodes = sourceNodeIds
    .map((nodeId) => context.view().by_id.get(nodeId))
    .filter((node): node is Node => Boolean(node));
  const spanIds = [...new Set(nodes.flatMap((node) => node.span_ids))];
  const errorSpanIds = [...new Set(nodes.flatMap((node) => node.error_span_ids))];
  const statusError = ["error", "failed", "failure"].includes(status.toLowerCase());
  return {
    service: nodes[0]?.service ?? "",
    has_error: errorSpanIds.length > 0 || statusError,
    error: errorSpanIds.length ? context.error_text(errorSpanIds[0]!) : status,
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

function itemPayload(
  context: TraceContext,
  item: TurnItem,
  findings: Record<string, Finding[]>,
): Record<string, unknown> {
  const itemFacts = facts(item.status, item.attributes, item.source_node_ids);
  const brief: string[] = [];
  if (item.kind === "model-call" && item.model) {
    itemFacts.model = item.model;
    brief.push(`model=${item.model}`);
  } else if (item.kind === "tool-call" && item.tool_call_id) {
    itemFacts.tool_call_id = item.tool_call_id;
  }
  const agentRuns = item.kind === "tool-call" || item.kind === "operation" ? item.agent_runs ?? [] : [];
  const operations = item.kind === "operation" ? item.operations ?? [] : [];
  if (operations.length) brief.push(`${operations.length} operation${operations.length === 1 ? "" : "s"}`);
  if (agentRuns.length) brief.push(`${agentRuns.length} agent run${agentRuns.length === 1 ? "" : "s"}`);
  const children = [
    ...operations.map((operation) => itemPayload(context, operation, findings)),
    ...agentRuns.map((run) => runPayload(context, run, findings)),
  ].sort((left, right) => (
    Number(left.start_ms) - Number(right.start_ms)
    || String(left.node_id).localeCompare(String(right.node_id))
  ));
  const source = sourcePayload(context, item.source_node_ids, findings, item.status);
  const detail = item.kind === "tool-call" ? toolNameDetail(item.input) : "";
  return {
    node_id: `agent-item:${item.kind}:${item.id}`,
    kind: item.kind,
    name: item.name,
    name_variants: compactNames(item, item.name, detail),
    start_ms: item.start_ms,
    duration_ms: item.duration_ms,
    brief: brief.join(" · "),
    facts: itemFacts,
    features: Object.fromEntries([
      ...(item.input === undefined ? [] : [["input", text(item.input)]]),
      ...(item.output === undefined ? [] : [["output", text(item.output)]]),
    ]),
    folded: 0,
    collapsed: item.kind === "operation" && children.length > 0
      && !source.has_error && !children.some(payloadHasError),
    children,
    ...source,
  };
}

function turnPayload(
  context: TraceContext,
  turn: AgentTurn,
  index: number,
  findings: Record<string, Finding[]>,
): Record<string, unknown> {
  const children = turn.items.map((item) => itemPayload(context, item, findings));
  const sources = turn.source_node_ids?.length
    ? turn.source_node_ids
    : [...new Set(turn.items.flatMap(itemSources))];
  const name = turn.name || `Turn ${index + 1}`;
  return {
    node_id: `agent-turn:${turn.id}`,
    kind: "agent-turn",
    name,
    name_variants: compactNames(turn, name),
    start_ms: turn.start_ms,
    duration_ms: turn.duration_ms,
    brief: `${turn.items.length} items`,
    facts: facts(turn.status, turn.attributes, sources),
    features: {},
    folded: 0,
    children,
    ...sourcePayload(context, sources, findings, turn.status),
  };
}

function itemSources(item: TurnItem): string[] {
  const agentRuns = item.kind === "tool-call" || item.kind === "operation" ? item.agent_runs ?? [] : [];
  const operations = item.kind === "operation" ? item.operations ?? [] : [];
  return [...new Set([
    ...(item.source_node_ids ?? []),
    ...operations.flatMap(itemSources),
    ...agentRuns.flatMap(runSources),
  ])];
}

function runSources(run: AgentRun): string[] {
  if (run.source_node_ids?.length) return run.source_node_ids;
  return [...new Set(run.items.flatMap((item) => (
    item.kind === "operation"
      ? itemSources(item)
      : item.items.flatMap(itemSources)
  )))];
}

function runPayload(
  context: TraceContext,
  run: AgentRun,
  findings: Record<string, Finding[]>,
): Record<string, unknown> {
  let turnIndex = 0;
  const children = run.items.map((item) => {
    if (item.kind === "operation") return itemPayload(context, item, findings);
    const payload = turnPayload(context, item, turnIndex, findings);
    turnIndex += 1;
    return payload;
  });
  const sources = runSources(run);
  const operationCount = run.items.filter((item) => item.kind === "operation").length;
  return {
    node_id: `agent-run:${run.id}`,
    kind: "agent-run",
    name: run.name,
    name_variants: compactNames(run, run.name),
    start_ms: run.start_ms,
    duration_ms: run.duration_ms,
    brief: `${turnIndex} turns · ${operationCount} operations`,
    facts: facts(run.status, run.attributes, sources),
    features: {},
    folded: 0,
    children,
    ...sourcePayload(context, sources, findings, run.status),
  };
}

export function agentRunRoots(
  context: TraceContext,
  ir: AgentRunIR,
  findings: Record<string, Finding[]>,
): Array<Record<string, unknown>> {
  return ir.runs.map((run) => runPayload(context, run, findings));
}
