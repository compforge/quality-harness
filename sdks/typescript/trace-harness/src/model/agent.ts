import type { TraceContext } from "./context";

export const AGENT_RUN_SCHEMA = "trace-harness/agent-run@1";

interface TurnItemBase {
  id: string;
  name: string;
  start_ms: number;
  duration_ms: number;
  status?: string;
  input?: unknown;
  output?: unknown;
  attributes?: Record<string, unknown>;
  source_node_ids?: string[];
}

export interface ModelCall extends TurnItemBase {
  kind: "model-call";
  model?: string;
}

export interface ToolCall extends TurnItemBase {
  kind: "tool-call";
  tool_call_id?: string;
  agent_runs?: AgentRun[];
}

export interface Operation extends TurnItemBase {
  kind: "operation";
  operations?: Operation[];
  agent_runs?: AgentRun[];
}

export type TurnItem = ModelCall | ToolCall | Operation;

export interface AgentTurn {
  kind: "agent-turn";
  id: string;
  name?: string;
  start_ms: number;
  duration_ms: number;
  status?: string;
  attributes?: Record<string, unknown>;
  source_node_ids?: string[];
  items: TurnItem[];
}

export type AgentRunItem = AgentTurn | Operation;

export interface AgentRun {
  id: string;
  name: string;
  start_ms: number;
  duration_ms: number;
  status?: string;
  attributes?: Record<string, unknown>;
  source_node_ids?: string[];
  items: AgentRunItem[];
}

export interface AgentRunIR {
  schema: typeof AGENT_RUN_SCHEMA;
  trace_id: string;
  runs: AgentRun[];
}

export function createAgentRunIR(traceId: string, runs: AgentRun[]): AgentRunIR {
  return { schema: AGENT_RUN_SCHEMA, trace_id: traceId, runs };
}

export function validateAgentRunIR(ir: AgentRunIR, context: TraceContext): AgentRunIR {
  if (ir.schema !== AGENT_RUN_SCHEMA) {
    throw new Error(`unsupported AgentRunIR schema: ${ir.schema}`);
  }
  if (ir.trace_id !== context.trace_id) {
    throw new Error(`AgentRunIR trace_id ${ir.trace_id} does not match context ${context.trace_id}`);
  }
  const knownNodes = new Set(context.nodes.map((node) => node.node_id));
  const runIds = new Set<string>();
  const turnIds = new Set<string>();
  const itemIds = new Set<string>();
  const sourceRefs = (owner: string, nodeIds: string[] = []) => {
    const missing = nodeIds.filter((nodeId) => !knownNodes.has(nodeId));
    if (missing.length) throw new Error(`${owner} references unknown node IDs: ${missing.join(", ")}`);
  };
  const timing = (owner: string, startMs: number, durationMs: number) => {
    if (!Number.isFinite(startMs)) throw new Error(`${owner} has invalid start_ms: ${startMs}`);
    if (!Number.isFinite(durationMs) || durationMs < 0) {
      throw new Error(`${owner} has invalid duration_ms: ${durationMs}`);
    }
  };
  const within = (
    parentOwner: string,
    parentStartMs: number,
    parentDurationMs: number,
    childOwner: string,
    childStartMs: number,
    childDurationMs: number,
  ) => {
    if (childStartMs < parentStartMs || childStartMs + childDurationMs > parentStartMs + parentDurationMs) {
      throw new Error(`${childOwner} falls outside ${parentOwner} time window`);
    }
  };
  const checkRuns = (
    owner: string,
    runs: AgentRun[],
    fieldName: string,
    parentWindow?: [number, number],
  ) => {
    let previousStart = Number.NEGATIVE_INFINITY;
    for (const run of runs) {
      if (run.start_ms < previousStart) throw new Error(`${owner}.${fieldName} must be ordered by start_ms`);
      previousStart = run.start_ms;
      checkRun(run);
      if (parentWindow) {
        within(owner, ...parentWindow, `AgentRun ${run.id}`, run.start_ms, run.duration_ms);
      }
    }
  };
  const checkItem = (item: TurnItem) => {
    if (itemIds.has(item.id)) throw new Error(`duplicate turn item id: ${item.id}`);
    itemIds.add(item.id);
    timing(`${item.kind} ${item.id}`, item.start_ms, item.duration_ms);
    sourceRefs(`${item.kind} ${item.id}`, item.source_node_ids);
    if (item.kind === "operation") {
      let previousStart = Number.NEGATIVE_INFINITY;
      for (const child of item.operations ?? []) {
        if (child.start_ms < previousStart) {
          throw new Error(`Operation ${item.id}.operations must be ordered by start_ms`);
        }
        previousStart = child.start_ms;
        checkItem(child);
        within(
          `Operation ${item.id}`,
          item.start_ms,
          item.duration_ms,
          `Operation ${child.id}`,
          child.start_ms,
          child.duration_ms,
        );
      }
    }
    if (item.kind === "tool-call" || item.kind === "operation") {
      checkRuns(
        `${item.kind} ${item.id}`,
        item.agent_runs ?? [],
        "agent_runs",
        [item.start_ms, item.duration_ms],
      );
    }
  };
  const checkRun = (run: AgentRun) => {
    if (runIds.has(run.id)) throw new Error(`duplicate AgentRun id: ${run.id}`);
    runIds.add(run.id);
    timing(`AgentRun ${run.id}`, run.start_ms, run.duration_ms);
    sourceRefs(`AgentRun ${run.id}`, run.source_node_ids);
    let previousRunItemStart = Number.NEGATIVE_INFINITY;
    for (const runItem of run.items) {
      if (runItem.start_ms < previousRunItemStart) {
        throw new Error(`AgentRun ${run.id} items must be ordered by start_ms`);
      }
      previousRunItemStart = runItem.start_ms;
      if (runItem.kind === "operation") {
        checkItem(runItem);
        within(
          `AgentRun ${run.id}`,
          run.start_ms,
          run.duration_ms,
          `operation ${runItem.id}`,
          runItem.start_ms,
          runItem.duration_ms,
        );
        continue;
      }
      const turn = runItem;
      if (turnIds.has(turn.id)) throw new Error(`duplicate AgentTurn id: ${turn.id}`);
      turnIds.add(turn.id);
      timing(`AgentTurn ${turn.id}`, turn.start_ms, turn.duration_ms);
      sourceRefs(`AgentTurn ${turn.id}`, turn.source_node_ids);
      within(
        `AgentRun ${run.id}`,
        run.start_ms,
        run.duration_ms,
        `AgentTurn ${turn.id}`,
        turn.start_ms,
        turn.duration_ms,
      );
      let previousItemStart = Number.NEGATIVE_INFINITY;
      for (const item of turn.items) {
        if (item.start_ms < previousItemStart) {
          throw new Error(`AgentTurn ${turn.id} items must be ordered by start_ms`);
        }
        previousItemStart = item.start_ms;
        checkItem(item);
        within(
          `AgentTurn ${turn.id}`,
          turn.start_ms,
          turn.duration_ms,
          `${item.kind} ${item.id}`,
          item.start_ms,
          item.duration_ms,
        );
      }
    }
  };
  checkRuns("AgentRunIR", ir.runs, "runs");
  return ir;
}

export function agentRunSnapshot(ir: AgentRunIR): Record<string, unknown> {
  return {
    schema: AGENT_RUN_SCHEMA,
    trace_id: ir.trace_id,
    runs: ir.runs.map(runSnapshot),
  };
}

function runSnapshot(run: AgentRun): Record<string, unknown> {
  return {
    id: run.id,
    name: run.name,
    start_ms: run.start_ms,
    duration_ms: run.duration_ms,
    status: run.status ?? "",
    attributes: run.attributes ?? {},
    source_node_ids: [...(run.source_node_ids ?? [])],
    items: run.items.map((runItem) => runItem.kind === "operation" ? itemSnapshot(runItem) : ({
      kind: runItem.kind,
      id: runItem.id,
      name: runItem.name ?? "",
      start_ms: runItem.start_ms,
      duration_ms: runItem.duration_ms,
      status: runItem.status ?? "",
      attributes: runItem.attributes ?? {},
      source_node_ids: [...(runItem.source_node_ids ?? [])],
      items: runItem.items.map(itemSnapshot),
    })),
  };
}

function itemSnapshot(item: TurnItem): Record<string, unknown> {
  return {
    kind: item.kind,
    id: item.id,
    name: item.name,
    start_ms: item.start_ms,
    duration_ms: item.duration_ms,
    status: item.status ?? "",
    input: item.input ?? null,
    output: item.output ?? null,
    attributes: item.attributes ?? {},
    source_node_ids: [...(item.source_node_ids ?? [])],
    ...(item.kind === "model-call" ? { model: item.model ?? null } : {}),
    ...(item.kind === "tool-call" ? {
      tool_call_id: item.tool_call_id ?? null,
      agent_runs: (item.agent_runs ?? []).map(runSnapshot),
    } : {}),
    ...(item.kind === "operation" ? {
      operations: (item.operations ?? []).map(itemSnapshot),
      agent_runs: (item.agent_runs ?? []).map(runSnapshot),
    } : {}),
  };
}
