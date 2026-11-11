/** Offline topology. Collection, metrics and diagnosis remain consumer-owned. */
export interface Node {
  readonly id: string;
  readonly kind: string;
}

export interface Relation {
  readonly id: string;
  readonly source: string;
  readonly target: string;
  readonly kind: string;
}

export interface GraphSnapshot {
  readonly schema_version: 1;
  readonly environment: string;
  readonly source?: string;
  readonly version?: string;
  readonly nodes: readonly Node[];
  readonly relations: readonly Relation[];
}

function segment(name: string): string {
  if (!name) throw new Error("graph names must not be empty");
  return encodeURIComponent(name).replace(/[!'()*]/g, c => `%${c.charCodeAt(0).toString(16).toUpperCase()}`);
}

export function serviceNodeId(name: string): string {
  return `service:${segment(name)}`;
}

export function operationNodeId(service: string, operation: string): string {
  return `operation:${segment(service)}/${segment(operation)}`;
}

/** @spec Identity is source/kind/target; repeated calls share a static edge. */
export function createRelation(source: string, target: string, kind: string): Relation {
  if ([source, target, kind].some(value => typeof value !== "string" || !value)) {
    throw new Error("relation endpoints and kind must be nonempty strings");
  }
  return { id: JSON.stringify([source, kind, target]), source, target, kind };
}

/**
 * An immutable topology snapshot for one environment.
 * @spec Kinds are open; built-in calls and belongs_to preserve endpoint and ownership constraints.
 * @spec Cycles are allowed; an operation has exactly one service owner.
 * @rule Topology does not prove actual traffic, metric coverage or causality.
 * @see {@link component://docs/graph.md}
 */
export class Graph {
  readonly environment: string;
  readonly source?: string;
  readonly version?: string;
  readonly nodes: readonly Node[];
  readonly relations: readonly Relation[];

  constructor(input: Omit<GraphSnapshot, "schema_version">) {
    if (typeof input.environment !== "string" || !input.environment) throw new Error("graph environment must not be empty");
    for (const value of [input.source, input.version]) {
      if (value !== undefined && typeof value !== "string") throw new Error("graph source and version must be strings");
    }
    this.environment = input.environment;
    this.source = input.source;
    this.version = input.version;
    this.nodes = Object.freeze(input.nodes.map(n => Object.freeze({ id: n.id, kind: n.kind })));
    this.relations = Object.freeze(input.relations.map(r => Object.freeze({ id: r.id, source: r.source, target: r.target, kind: r.kind })));
    const nodes = new Map<string, Node>();
    for (const node of this.nodes) {
      if (typeof node.id !== "string" || !node.id || typeof node.kind !== "string" || !node.kind) throw new Error("node id and kind must be nonempty strings");
      if (nodes.has(node.id)) throw new Error(`duplicate node id: ${node.id}`);
      nodes.set(node.id, node);
    }
    const ids = new Set<string>();
    const owners = new Set<string>();
    for (const relation of this.relations) {
      if (typeof relation.kind !== "string" || !relation.kind) throw new Error("relation kind must be a nonempty string");
      if (relation.id !== createRelation(relation.source, relation.target, relation.kind).id) throw new Error(`relation id does not match endpoints/kind: ${relation.id}`);
      if (ids.has(relation.id)) throw new Error(`duplicate relation: ${relation.id}`);
      ids.add(relation.id);
      const source = nodes.get(relation.source), target = nodes.get(relation.target);
      if (!source || !target) throw new Error(`unknown relation endpoint: ${relation.id}`);
      if (relation.kind === "calls" && (source.kind !== "operation" || target.kind !== "operation")) throw new Error(`calls requires operation endpoints: ${relation.id}`);
      if (relation.kind === "belongs_to") {
        if (source.kind !== "operation" || target.kind !== "service") throw new Error(`belongs_to requires operation -> service: ${relation.id}`);
        if (owners.has(source.id)) throw new Error(`operation has multiple services: ${source.id}`);
        owners.add(source.id);
      }
    }
    for (const node of this.nodes) {
      if (node.kind === "operation" && !owners.has(node.id)) throw new Error(`operation has no service: ${node.id}`);
    }
    Object.freeze(this);
  }

  node(id: string): Node | undefined { return this.nodes.find(node => node.id === id); }

  relationsFrom(id: string, kind?: string): readonly Relation[] {
    return this.relations.filter(r => r.source === id && (kind === undefined || r.kind === kind));
  }

  relationsTo(id: string, kind?: string): readonly Relation[] {
    return this.relations.filter(r => r.target === id && (kind === undefined || r.kind === kind));
  }

  /** Reachable nodes include the root and retain declaration order. */
  reachableFrom(id: string, kind?: string): readonly Node[] {
    if (!this.node(id)) throw new Error(`unknown root: ${id}`);
    const seen = new Set([id]), pending = [id];
    const adjacency = new Map<string, string[]>();
    for (const relation of this.relations) {
      if (kind !== undefined && relation.kind !== kind) continue;
      const targets = adjacency.get(relation.source) ?? [];
      targets.push(relation.target);
      adjacency.set(relation.source, targets);
    }
    while (pending.length) {
      for (const target of adjacency.get(pending.pop()!) ?? []) {
        if (!seen.has(target)) { seen.add(target); pending.push(target); }
      }
    }
    return this.nodes.filter(node => seen.has(node.id));
  }

  /** Include operation owners without adding their unrelated operations. */
  subgraph(ids: Iterable<string>): Graph {
    const selected = new Set(ids);
    for (const id of selected) if (!this.node(id)) throw new Error(`unknown node: ${id}`);
    for (const relation of this.relations) {
      if (relation.kind === "belongs_to" && selected.has(relation.source)) selected.add(relation.target);
    }
    return new Graph({ environment: this.environment, source: this.source, version: this.version,
      nodes: this.nodes.filter(n => selected.has(n.id)),
      relations: this.relations.filter(r => selected.has(r.source) && selected.has(r.target)),
    });
  }

  toJSON(): GraphSnapshot {
    return { schema_version: 1, environment: this.environment,
      ...(this.source === undefined ? {} : { source: this.source }),
      ...(this.version === undefined ? {} : { version: this.version }),
      nodes: this.nodes.map(n => ({ ...n })), relations: this.relations.map(r => ({ ...r })),
    };
  }

  static fromJSON(value: unknown): Graph {
    if (!value || typeof value !== "object" || Array.isArray(value)) throw new Error("expected graph object");
    const data = value as Record<string, unknown>;
    if (data.schema_version !== 1) throw new Error("unsupported graph schema_version");
    if (!Array.isArray(data.nodes) || !Array.isArray(data.relations)) throw new Error("expected graph nodes and relations arrays");
    for (const item of [...data.nodes, ...data.relations]) {
      if (!item || typeof item !== "object" || Array.isArray(item)) throw new Error("expected node or relation object");
    }
    // The constructor validates field values and all graph invariants.
    return new Graph(data as unknown as GraphSnapshot);
  }
}
