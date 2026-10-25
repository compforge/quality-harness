import { renderHTML, validate, type Document, type Item, type Run, type Node, type Status } from "@compforge/agent-view";
import type { CallRequest, CallResponse, JsonValue } from "@compforge/agent-common";
import { validateAgentRunIR, type AgentRunIR, type AgentRun, type AgentTurn, type TurnItem } from "./model/agent";
import type { TraceContext } from "./model/context";

/**
 * @spec Export only observed trace facts; preserve source identities and unknown values.
 * @see {@link repo://docs/agent-view.md}
 */
export function agentView(ir: AgentRunIR, context: TraceContext): Document {
  validateAgentRunIR(ir, context);
  function base(node: AgentRun | AgentTurn | TurnItem, kind: string): Node {
    const raw = (node.status ?? "").toLowerCase();
    const aliases: Record<string, string> = {ok: "completed", success: "completed", error: "failed", failure: "failed"};
    const status = aliases[raw] ?? raw;
    const attributes = JSON.parse(JSON.stringify(node.attributes ?? {})) as Record<string, JsonValue>;
    attributes.trace_id = ir.trace_id;
    attributes.trace_timing = {start_ms: node.start_ms, duration_ms: node.duration_ms};
    const result: Node = {id: `${ir.trace_id}:${kind}:${node.id}`, name: node.name || node.id, status: "unknown", attributes,
      evidence: (node.source_node_ids ?? []).map(id => ({source: "trace-node", id, uri: `trace:${ir.trace_id}`}))};
    if (["running", "completed", "failed", "cancelled", "outcome_unknown"].includes(status)) result.status = status as Status;
    else if (raw) attributes.source_status = node.status!;
    if (node.start_ms > 0) {
      result.started_at = new Date(node.start_ms).toISOString();
      result.finished_at = new Date(node.start_ms + node.duration_ms).toISOString();
    }
    return result;
  }
  function run(node: AgentRun): Run { return {...base(node, "run"), kind: "run", lanes: [], items: node.items.map(item)}; }
  function item(node: AgentTurn | TurnItem): Item {
    if (node.kind === "agent-turn") return {...base(node, "turn"), kind: "turn", items: node.items.map(item)};
    if (node.kind === "operation") return {...base(node, "operation"), kind: "operation", operation: node.name || "operation",
      data: JSON.parse(JSON.stringify({input: node.input ?? null, output: node.output ?? null})),
      items: [...(node.operations ?? []).map(item), ...(node.agent_runs ?? []).map(run)]};
    const request: CallRequest = {};
    const response: CallResponse = {};
    if (node.input !== undefined && node.input !== null) request.input = JSON.parse(JSON.stringify(node.input));
    if (node.output !== undefined && node.output !== null) response.output = JSON.parse(JSON.stringify(node.output));
    if (node.kind === "tool-call") request.tool_name = node.name;
    const calls = {...(Object.keys(request).length ? {request} : {}), ...(Object.keys(response).length ? {response} : {})};
    if (node.kind === "model-call") {
      const facts = base(node, "model_call");
      if (node.model) facts.attributes!.trace_model = node.model;
      return {...facts, kind: "model_call", ...calls};
    }
    return {...base(node, "tool_call"), kind: "tool_call", ...calls, ...(node.tool_call_id ? {tool_call_id: node.tool_call_id} : {}), runs: (node.agent_runs ?? []).map(run)};
  }
  const document: Document = {schema_version: "agent-ledger/view@1", runs: ir.runs.map(run), issues: []};
  validate(document);
  return document;
}

export function renderAgentView(ir: AgentRunIR, context: TraceContext, title = "Trace agent execution"): string {
  return renderHTML(agentView(ir, context), title);
}
