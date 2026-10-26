import { validate, type SessionView, type Item, type Node, type Status } from "@compforge/agent-observation";
import type { CallRequest, CallResponse, JsonValue, SessionRef } from "@compforge/agent-common";
import { validateAgentRunIR, type AgentRunIR, type AgentRun, type AgentTurn, type TurnItem } from "./model/agent";
import type { TraceContext } from "./model/context";

/**
 * @spec Export only observed trace facts; preserve source identities and unknown values.
 * @see {@link repo://docs/session-view.md}
 */
export function sessionViews(ir: AgentRunIR, context: TraceContext): SessionView[] {
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
    if ("kind" in node && (node.kind === "tool-call" || node.kind === "operation") && node.agent_runs?.length) {
      attributes.trace_child_runs=node.agent_runs.map(run=>({id:run.id,session:run.session?{...run.session}:null}));
    }
    return result;
  }
  function item(node: AgentTurn | TurnItem, owner: SessionRef): Item {
    if (node.kind === "agent-turn") return {...base(node, "turn"), kind: "turn", items: node.items.map(x => item(x, owner))};
    if (node.kind === "operation") return {...base(node, "operation"), kind: "operation", operation: node.name || "operation",
      data: JSON.parse(JSON.stringify({input: node.input ?? null, output: node.output ?? null})),
      items: (node.operations ?? []).map(x => item(x, owner))};
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
    return {...base(node, "tool_call"), kind: "tool_call", ...calls, ...(node.tool_call_id ? {tool_call_id: node.tool_call_id} : {}), sessions: (node.agent_runs ?? []).flatMap(run => run.session && (run.session.source !== owner.source || run.session.id !== owner.id) ? [{...run.session}] : [])};
  }
  const grouped = new Map<string, SessionView>();
  function children(items: Array<AgentTurn | TurnItem>): void {
    for (const node of items) {
      if (node.kind === "agent-turn") children(node.items);
      else if (node.kind === "operation") {children(node.operations ?? []); for (const child of node.agent_runs ?? []) visit(child);}
      else if (node.kind === "tool-call") for (const child of node.agent_runs ?? []) visit(child);
    }
  }
  function visit(run: AgentRun): void {
    if (run.session) {
      const key=JSON.stringify([run.session.source,run.session.id]);
      let view=grouped.get(key);
      if (!view) {view={schema_version:"agent-ledger/session-view@1",session:{...run.session},items:[]};grouped.set(key,view);}
      const facts=base(run,"run");facts.attributes!.run_id=run.id;
      view.items.push({...facts,kind:"operation",operation:"trace.run",items:run.items.map(x=>item(x,run.session!))});
    }
    children(run.items);
  }
  for (const run of ir.runs) visit(run);
  const views=[...grouped.values()];
  for (const view of views) validate(view);
  return structuredClone(views);
}
