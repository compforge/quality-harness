import type { Field, Finding, Node } from "../model/node";
import type { ViewTree } from "../model/viewtree";

export type TracePerspective = "full" | "agent";
export type PerspectiveLevel = "primary" | "context" | "detail";

export type ChildOp =
  | { type: "expand"; node: Node }
  | { type: "fold"; node: Node }
  | { type: "aggregate"; nodes: Node[]; label?: string }
  | { type: "summarize"; node: Node; line: Field[] }
  | { type: "hide"; node: Node }
  | { type: "group"; nodes: Node[]; label: string; brief?: Field[]; collapsed?: boolean };

export interface RenderConfig {
  prune_below_ms?: number;
  max_depth?: number;
  expand?: Set<string>;
  perspective?: TracePerspective;
}

export interface RenderContext {
  view: ViewTree;
  findings: Record<string, Finding[]>;
  flagged: Map<string, boolean>;
  config: RenderConfig;
}

export abstract class Facet {
  priority = 0;
  abstract match(node: Node): boolean;

  brief(node: Node): Field[] {
    return node.brief;
  }

  perspectiveLevel(
    _node: Node,
    _perspective: TracePerspective,
  ): PerspectiveLevel | undefined {
    return undefined;
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
