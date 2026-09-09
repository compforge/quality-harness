import type { TraceContext } from "./context";
import type { Field, Finding, Node } from "./node";
import type { NormSpan } from "./span";

export interface KindSpec {
  kind: string;
  matches(span: NormSpan): boolean;
  claims?(primary: NormSpan, candidates: NormSpan[]): Set<string>;
  build?(primary: NormSpan, satellites: NormSpan[]): Record<string, unknown>;
  metrics?: Record<string, (node: Node) => number | undefined>;
  strategy?: Record<string, "ratio" | "topn">;
  rules?: Array<(node: Node, context: TraceContext) => Finding[]>;
  obs_hole?: boolean;
  project?(node: Node): Field[];
}

export class SpecSet implements Iterable<KindSpec> {
  readonly #specs: KindSpec[];
  readonly #byKind: Map<string, KindSpec>;

  constructor(specs: Iterable<KindSpec>) {
    this.#specs = [...specs];
    this.#byKind = new Map(this.#specs.map((spec) => [spec.kind, spec]));
  }

  classify(span: NormSpan): KindSpec | undefined {
    return this.#specs.find((spec) => spec.matches(span));
  }

  get(kind: string): KindSpec | undefined {
    return this.#byKind.get(kind);
  }

  [Symbol.iterator](): Iterator<KindSpec> {
    return this.#specs[Symbol.iterator]();
  }
}

export function mergeSpecs(...sets: SpecSet[]): SpecSet {
  return new SpecSet(sets.flatMap((set) => [...set]));
}
