import type { TraceContext } from "../model/context";
import type { Node } from "../model/node";

export class EvidenceDependency {
  constructor(readonly span_ids: readonly string[], readonly fields: readonly string[] | null) {}
}
export class FactDependency {
  constructor(readonly node_id: string, readonly name: string) {}
}
export type Dependency = EvidenceDependency | FactDependency;
/** Business declares evidence and pure computation; the runtime schedules reads. */
export interface FactProducer {
  produces: readonly string[];
  applies(node: Node): boolean;
  requires(node: Node, trace: TraceContext): readonly Dependency[];
  compute(node: Node, trace: TraceContext): Record<string, unknown>;
}
