import { validate, type Node as ObservationNode, type Operation, type Turn, type SessionView } from "@compforge/agent-observation";
import type { TraceContext } from "./model/context";

export function* walkNodes(nodes: readonly ObservationNode[]): Generator<ObservationNode> {
  for (const item of nodes) {
    yield item;
    if (item.kind === "turn" || item.kind === "operation") yield* walkNodes((item as Operation | Turn).nodes ?? []);
  }
}

export function sourceNodeIds(item: ObservationNode, context: TraceContext): string[] {
  return [...new Set((item.evidence ?? []).filter(ref => ref.source === "trace-node"
    && (ref.uri === undefined || ref.uri === `trace:${context.trace_id}`)).map(ref => ref.id))];
}

/**
 * @spec Extractors return shared SessionViews; Trace validates only local evidence ownership.
 * @see docs/session-view.md
 */
export function validateSessionViews(views: readonly SessionView[], context: TraceContext): SessionView[] {
  const result = structuredClone([...views]);
  const seen = new Set<string>();
  for (const view of result) {
    validate(view);
    const identity = JSON.stringify([view.session.source, view.session.id]);
    if (seen.has(identity)) throw new Error(`Trace ${context.trace_id}: duplicate Session ${identity}`);
    seen.add(identity);
    for (const item of walkNodes(view.nodes)) {
      const missing = sourceNodeIds(item, context).filter(id => !context.tree().by_id.has(id));
      if (missing.length) throw new Error(`Trace ${context.trace_id}, Session ${identity}, item ${item.id}: unknown node IDs: ${missing.join(", ")}`);
    }
  }
  return result;
}
