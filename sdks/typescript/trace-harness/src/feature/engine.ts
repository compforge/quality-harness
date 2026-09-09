import type { Node } from "../model/node";
import { buildView, type ViewTree } from "../model/viewtree";
import { builtinFeatures } from "./builtins";
import { FeatureContext } from "./context";
import { FeatureRegistry } from "./registry";

export function bakeFeatures(
  nodes: Node[],
  raw?: (spanId: string) => Record<string, unknown>,
  registry?: FeatureRegistry,
): void {
  const activeRegistry = registry ?? new FeatureRegistry(builtinFeatures());
  const context = new FeatureContext(buildView(nodes), activeRegistry, raw);
  const eager = activeRegistry.registered().filter((feature) => feature.bake !== false);
  for (const node of nodes) {
    for (const feature of eager) {
      if (!feature.applies(node)) continue;
      for (const name of feature.produces) {
        const value = context.get(node, name);
        if (value !== undefined) node.facts[name] = value;
      }
    }
  }
}

export function lazyFeatures(
  node: Node,
  view: ViewTree,
  raw?: (spanId: string) => Record<string, unknown>,
  registry?: FeatureRegistry,
): Record<string, unknown> {
  const activeRegistry = registry ?? new FeatureRegistry(builtinFeatures());
  const context = new FeatureContext(view, activeRegistry, raw);
  const output: Record<string, unknown> = {};
  for (const feature of activeRegistry.registered()) {
    if (feature.bake !== false || !feature.applies(node)) continue;
    for (const name of feature.produces) output[name] = context.get(node, name);
  }
  return output;
}
