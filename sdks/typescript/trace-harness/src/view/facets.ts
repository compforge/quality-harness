import type { Node } from "../model/node";
import {
  DefaultFacet,
  type ChildOp,
  type PerspectiveLevel,
  type RenderContext,
  type TracePerspective,
} from "./facet";
import type { Facet } from "./facet";

class ServiceFacet extends DefaultFacet {
  override priority = 10;
  match(node: Node): boolean {
    return node.kind === "service";
  }
}

class ModelCallFacet extends DefaultFacet {
  override priority = 20;
  match(node: Node): boolean {
    return node.kind === "model-call";
  }

  override perspectiveLevel(
    _node: Node,
    perspective: TracePerspective,
  ): PerspectiveLevel | undefined {
    return perspective === "agent" ? "primary" : undefined;
  }

  override layout(node: Node, children: Node[], context: RenderContext): ChildOp[] {
    const http = children.filter((child) => child.kind === "http")
      .map((child): ChildOp => ({ type: "hide", node: child }));
    const rest = children.filter((child) => child.kind !== "http");
    return [...http, ...super.layout(node, rest, context)];
  }
}

class AgentFacet extends DefaultFacet {
  override priority = 20;
  match(node: Node): boolean {
    return node.kind === "agent";
  }

  override perspectiveLevel(
    _node: Node,
    perspective: TracePerspective,
  ): PerspectiveLevel | undefined {
    return perspective === "agent" ? "primary" : undefined;
  }
}

class ToolCallFacet extends DefaultFacet {
  override priority = 20;
  match(node: Node): boolean {
    return node.kind === "tool-call";
  }

  override perspectiveLevel(
    _node: Node,
    perspective: TracePerspective,
  ): PerspectiveLevel | undefined {
    return perspective === "agent" ? "primary" : undefined;
  }
}

export function builtinFacets(): Facet[] {
  return [new ServiceFacet(), new ModelCallFacet(), new AgentFacet(), new ToolCallFacet()];
}
