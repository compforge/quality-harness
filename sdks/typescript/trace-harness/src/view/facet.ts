import type { Field, Finding, Node } from "../model/node";
import type { NodeTree } from "../model/node-tree";


export type ChildOp =
  | { type: "expand"; node: Node }
  | { type: "fold"; node: Node }
  /** Fixed ×N / sum summary, folded by default; children remain available for expansion. */
  | { type: "aggregate"; nodes: Node[]; label?: string }
  | { type: "summarize"; node: Node; line: Field[] }
  | { type: "hide"; node: Node }
  | { type: "group"; nodes: Node[]; label: string; brief?: Field[]; collapsed?: boolean; children?: ChildOp[] };

export interface RenderConfig {
  prune_below_ms?: number;
  max_depth?: number;
  expand?: Set<string>;
}

export interface RenderContext {
  view: NodeTree;
  findings: Readonly<Record<string, readonly Finding[]>>;
  flagged: Map<string, boolean>;
  config: RenderConfig;
}

export abstract class Facet {
  priority = 0;
  abstract match(node: Node): boolean;

  brief(node: Node): Field[] {
    return node.brief;
  }


  layout(_node: Node, children: Node[], context: RenderContext): ChildOp[] {
    const cut = context.config.prune_below_ms;
    return children.map((child) =>
      cut !== undefined && child.duration_ms < cut && !context.flagged.get(child.node_id)
        ? { type: "fold", node: child }
        : { type: "expand", node: child },
    );
  }

}

export class DefaultFacet extends Facet {
  override priority = Number.MIN_SAFE_INTEGER;
  match(_node: Node): boolean {
    return true;
  }
}
