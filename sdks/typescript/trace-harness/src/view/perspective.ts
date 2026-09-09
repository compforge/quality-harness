import type { Node } from "../model/node";
import type { DisplayNode } from "./display";
import type { TracePerspective } from "./facet";
import type { FacetRegistry } from "./registry";

interface ProjectedNode {
  display: DisplayNode;
  hidden: number;
}

function sourceCount(display: DisplayNode): number {
  return Math.max(display.node_ids.length, 1);
}

function connector(
  hidden: number,
  nodeIds: string[],
  children: DisplayNode[],
): DisplayNode {
  return {
    kind: "",
    name: `… +${hidden} 个上下文节点`,
    brief: [],
    node_ids: [...new Set(nodeIds)],
    children,
    findings: [],
    folded: 0,
  };
}

/**
 * Projects one immutable analysis tree into a presentation tree.
 *
 * Non-primary paths stay represented by synthetic connector rows, so the projection never
 * rewrites Node.parent_node_id or invents an alternative analysis topology.
 */
export function projectPerspective(
  roots: DisplayNode[],
  byId: Map<string, Node>,
  registry: FacetRegistry,
  perspective: TracePerspective,
): DisplayNode[] {
  if (perspective === "full") return roots;

  const visit = (display: DisplayNode): ProjectedNode | undefined => {
    const projectedChildren = display.children
      .map(visit)
      .filter((item): item is ProjectedNode => Boolean(item));
    const node = display.kind && display.node_ids.length
      ? byId.get(display.node_ids[0]!)
      : undefined;
    const level = node ? registry.perspectiveLevel(node, perspective) : "detail";
    if (level === "primary" || (level === "context" && projectedChildren.length)) {
      return {
        display: {
          ...display,
          children: projectedChildren.map((item) => item.display),
          folded: 0,
        },
        hidden: 0,
      };
    }
    if (!projectedChildren.length) return undefined;

    const ownHidden = sourceCount(display);
    if (projectedChildren.length === 1 && projectedChildren[0]!.hidden > 0) {
      const child = projectedChildren[0]!;
      const hidden = ownHidden + child.hidden;
      return {
        display: connector(
          hidden,
          [...display.node_ids, ...child.display.node_ids],
          child.display.children,
        ),
        hidden,
      };
    }
    return {
      display: connector(
        ownHidden,
        display.node_ids,
        projectedChildren.map((item) => item.display),
      ),
      hidden: ownHidden,
    };
  };

  return roots.map(visit)
    .filter((item): item is ProjectedNode => Boolean(item))
    .map((item) => item.display);
}
