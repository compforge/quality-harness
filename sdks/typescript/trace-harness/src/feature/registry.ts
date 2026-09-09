import type { Node } from "../model/node";
import type { Feature } from "./feature";

export class FeatureRegistry {
  readonly #features: Feature[];

  constructor(features: Iterable<Feature> = []) {
    this.#features = [...features];
  }

  register(feature: Feature): Feature {
    this.#features.push(feature);
    return feature;
  }

  registered(): Feature[] {
    return [...this.#features];
  }

  producing(name: string, node: Node): Feature | undefined {
    return this.#features.find((feature) => feature.produces.includes(name) && feature.applies(node));
  }
}
