import type { Node } from "../model/node";
import type { ViewTree } from "../model/viewtree";
import type { FeatureRegistry } from "./registry";

export class FeatureContext {
  readonly #memo = new Map<string, unknown>();
  readonly #view: ViewTree;
  readonly #raw?: (spanId: string) => Record<string, unknown>;
  readonly #registry: FeatureRegistry;

  constructor(
    view: ViewTree,
    registry: FeatureRegistry,
    raw?: (spanId: string) => Record<string, unknown>,
  ) {
    this.#view = view;
    this.#raw = raw;
    this.#registry = registry;
  }

  children(node: Node): Node[] {
    return this.#view.children(node);
  }

  raw(spanId: string): Record<string, unknown> {
    return this.#raw?.(spanId) ?? {};
  }

  get(node: Node, name: string): unknown {
    const key = `${node.node_id}\u0000${name}`;
    if (this.#memo.has(key)) return this.#memo.get(key);
    if (Object.hasOwn(node.facts, name)) {
      const value = node.facts[name];
      this.#memo.set(key, value);
      return value;
    }
    this.#memo.set(key, undefined);
    const feature = this.#registry.producing(name, node);
    if (feature) {
      for (const [produced, value] of Object.entries(feature.compute(node, this) ?? {})) {
        this.#memo.set(`${node.node_id}\u0000${produced}`, value);
      }
    }
    return this.#memo.get(key);
  }
}
