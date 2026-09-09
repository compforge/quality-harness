import type { Field, Finding } from "../model/node";

export interface Compact {
  // expect is a target upper bound; implementations own their semantic views
  // and may return an actual ratio well below it. Serializers use actual as the
  // next breakpoint, so it must describe the returned projection truthfully.
  compact(expect: number): readonly [name: string, actual: number];
}

function nameLength(value: string): number {
  return [...value].reduce((length, character) => (
    length + (character.codePointAt(0)! > 255 ? 2 : 1)
  ), 0);
}

function clampRatio(value: number): number {
  return Math.max(0, Math.min(1, value));
}

export class DisplayName implements Compact {
  constructor(
    readonly name: string,
    readonly detail = "",
  ) {}

  compact(expect: number): readonly [name: string, actual: number] {
    const candidates = this.detail
      ? [`${this.name} · ${this.detail}`, this.detail, this.name]
      : [this.name];
    const rawLength = Math.max(1, nameLength(candidates[0]!));
    const projections = [...new Set(candidates)].map((name) => (
      [name, nameLength(name) / rawLength] as const
    ));
    return projections.find(([, actual]) => actual <= clampRatio(expect))
      ?? projections.reduce((shortest, projection) => (
        projection[1] < shortest[1] ? projection : shortest
      ));
  }
}

export interface DisplayNode extends Partial<Compact> {
  kind: string;
  name: string;
  brief: Field[];
  node_ids: string[];
  children: DisplayNode[];
  findings: Finding[];
  folded: number;
}

export function nameProjections(named: DisplayNode | Compact, defaultName = ""): string[] {
  const owner: Compact = typeof named.compact === "function"
    ? named as Compact
    : new DisplayName(defaultName || (named as DisplayNode).name);
  const compact = (expect: number) => owner.compact(expect);
  const [raw, rawActual] = compact(1);
  const rawLength = Math.max(1, nameLength(raw));
  const projections = [raw];
  // Offline HTML cannot call the original object. Use each returned ratio as the
  // next breakpoint instead of probing every display-column budget.
  let budget = Math.ceil(rawActual * rawLength) - 1;
  while (budget >= 0) {
    const expect = budget / rawLength;
    const [name, actual] = compact(expect);
    if (!projections.includes(name)) projections.push(name);
    if (actual > expect) break;
    budget = Math.min(budget - 1, Math.ceil(actual * rawLength) - 1);
  }
  return projections;
}
