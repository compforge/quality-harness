import type { TraceContext } from "./model/context";
import type { Dependency } from "./loading/facts";
import type { Node } from "./model/node";
import type { ViewTree } from "./model/viewtree";

/** Named fact outputs; scheduling and presentation belong to consumers. */
export interface FactTransform {
  produces: readonly string[];
  requires?(node: Node, trace: TraceContext): readonly Dependency[];
  applies(node: Node): boolean;
  compute(node: Node, context: TransformContext): Record<string, unknown>;
}

export class TransformContext {
  readonly #owners = new Map<Node, Map<string, FactTransform>>();
  readonly #pending = new Map<Node, Record<string, unknown>>();
  readonly #active = new Map<Node, Set<FactTransform>>();
  readonly #completed = new Map<Node, Set<FactTransform>>();
  readonly #done = new Map<Node, Set<FactTransform>>();
  constructor(readonly view: ViewTree, transforms: Iterable<FactTransform>) {
    const items = [...transforms];
    for (const node of view.by_id.values()) {
      const owners = new Map<string, FactTransform>();
      this.#owners.set(node, owners);
      this.#active.set(node, new Set());
      this.#done.set(node, new Set());
      for (const transform of items.filter((item) => item.applies(node))) {
        for (const name of transform.produces) {
          if (Object.hasOwn(node.facts, name) || owners.has(name)) throw new Error(`conflicting fact producer: ${node.node_id}:${name}`);
          owners.set(name, transform);
        }
      }
    }
  }
  children(node: Node): Node[] { return this.view.children(node); }
  /** Resolve dependencies using standardized facts, never raw protocol data. */
  get(node: Node, name: string): unknown {
    if (Object.hasOwn(node.facts, name)) return node.facts[name];
    const pending = this.#pending.get(node);
    if (pending && Object.hasOwn(pending, name)) return pending[name];
    const producer = this.#owners.get(node)?.get(name);
    if (!producer) return undefined;
    const active = this.#active.get(node)!;
    if (active.has(producer)) throw new Error(`cyclic fact dependency: ${node.node_id}:${name}`);
    if (!this.#done.get(node)!.has(producer) && !this.#completed.get(node)?.has(producer)) {
      active.add(producer);
      try {
        const values = producer.compute(node, this);
        if (Object.keys(values).some((key) => !producer.produces.includes(key))) throw new Error(`undeclared fact output: ${node.node_id}:${name}`);
        this.#pending.set(node, Object.assign(this.#pending.get(node) ?? {}, values));
        const completed = this.#completed.get(node) ?? new Set<FactTransform>();
        completed.add(producer); this.#completed.set(node, completed);
      } finally { active.delete(producer); }
    }
    return this.#pending.get(node)?.[name];
  }
  /** Commit an explicitly requested batch atomically, including dependency outputs. */
  materialize(requests: Iterable<readonly [Node, string]>): void {
    try {
      for (const [node, name] of requests) {
        if (this.view.by_id.get(node.node_id) !== node) throw new Error(`node does not belong to this trace: ${node.node_id}`);
        this.get(node, name);
      }
      for (const [node, values] of this.#pending) for (const name of Object.keys(values)) {
        if (Object.hasOwn(node.facts, name)) throw new Error(`conflicting fact write: ${node.node_id}:${name}`);
      }
      for (const [node, values] of this.#pending) Object.assign(node.facts, values);
      for (const [node, producers] of this.#completed) for (const producer of producers) this.#done.get(node)!.add(producer);
    } finally { this.#pending.clear(); this.#completed.clear(); }
  }
}
export function builtinTransforms(): FactTransform[] {
  return [{
    produces: ["http_status"], applies: (node) => node.kind === "model-call",
    compute: (node, context) => {
      const statuses = context.children(node).filter((child) => child.kind === "http")
        .map((child) => context.get(child, "status")).filter((s) => s != null).map(Number);
      return statuses.length ? { http_status: statuses.find((s) => s !== 200) ?? statuses[0] } : {};
    },
  }];
}
