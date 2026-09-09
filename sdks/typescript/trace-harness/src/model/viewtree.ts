import type { Node } from "./node";
import type { TraceContext } from "./context";

export interface NodeTreeExtractor<T> {
  /** Deterministically extract one concern-specific IR from the complete node tree. */
  extract(context: TraceContext): T;
}

export class ViewTree {
  readonly roots: Node[];
  readonly by_id: Map<string, Node>;
  readonly by_span: Map<string, Node>;
  readonly #children: Map<string, Node[]>;

  constructor(nodes: Node[]) {
    this.by_id = new Map(nodes.map((node) => [node.node_id, node]));
    this.by_span = new Map(nodes.flatMap((node) => node.span_ids.map((id) => [id, node] as const)));
    this.#children = new Map();
    this.roots = [];
    for (const node of nodes) {
      if (node.parent_node_id && this.by_id.has(node.parent_node_id)) {
        const children = this.#children.get(node.parent_node_id) ?? [];
        children.push(node);
        this.#children.set(node.parent_node_id, children);
      } else {
        this.roots.push(node);
      }
    }
    this.roots.sort((a, b) => a.start_ms - b.start_ms);
    for (const children of this.#children.values()) children.sort((a, b) => a.start_ms - b.start_ms);
  }

  children(node: Node): Node[] {
    return this.#children.get(node.node_id) ?? [];
  }
}

export function buildView(nodes: Node[]): ViewTree {
  return new ViewTree(nodes);
}
