import type { Node } from "../model/node";
import {
  DefaultFacet,
  type Facet,
  type PerspectiveLevel,
  type TracePerspective,
} from "./facet";

export class FacetRegistry {
  readonly #facets: Facet[] = [];
  readonly #default = new DefaultFacet();

  constructor(facets: Iterable<Facet> = []) {
    for (const facet of facets) this.register(facet);
  }

  register(facet: Facet): void {
    this.#facets.push(facet);
    this.#facets.sort((a, b) => b.priority - a.priority);
  }

  dispatch(node: Node): Facet {
    return this.#facets.find((facet) => facet.match(node)) ?? this.#default;
  }

  perspectiveLevel(node: Node, perspective: TracePerspective): PerspectiveLevel {
    if (perspective === "full") return "primary";
    for (const facet of this.#facets) {
      if (!facet.match(node)) continue;
      const level = facet.perspectiveLevel(node, perspective);
      if (level) return level;
    }
    return this.#default.perspectiveLevel(node, perspective) ?? "detail";
  }
}
