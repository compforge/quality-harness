import type { Node } from "../model/node";
import type { FeatureContext } from "./context";

export interface Feature {
  produces: readonly string[];
  applies(node: Node): boolean;
  compute(node: Node, context: FeatureContext): Record<string, unknown>;
  bake?: boolean;
}
