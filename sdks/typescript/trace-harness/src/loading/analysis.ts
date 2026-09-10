import type { TraceHarness } from "../harness";
import type { TraceContext } from "../model/context";
import type { Node } from "../model/node";
import { spanErrorText } from "../model/span";
import { Measurements, type Measurement } from "../model/measurement";
import { measure } from "../analyze/measure";
import type { EvidenceLoader } from "./loader";
import { EvidenceDependency, FactDependency, type Dependency, type FactProducer } from "./facts";
import { httpEndpoint } from "../kinds/http";
import { EvidenceTooLarge } from "./model";

const HTTP_EVIDENCE: FactProducer = {
  produces: ["http_evidence"], applies: () => true,
  requires: (_node, trace) => {
    const endpoints = new Set([...trace.spans].filter(([, span]) => httpEndpoint(span)).map(([id]) => id));
    const ids = [...trace.spans].filter(([id, span]) => endpoints.has(id)
      || span.parent_span_id && endpoints.has(span.parent_span_id) && ["request-body", "response-body"].includes(span.name)).map(([id]) => id);
    return [new EvidenceDependency(ids, ["http.request.header.accept", "http.response.header.content-type",
      "http.response.header.content_type", "http.request.headers", "http.response.headers", "http.request.body", "http.request.body.json"])];
  }, compute: () => ({}),
};

/** One lease owns computed state; persisted evidence may be reused by other leases. */
export class TraceAnalysis {
  readonly measurements = new Measurements();
  readonly #facts = new Map<string, Promise<unknown>>();
  readonly #producers = new Map<Node, Map<object, Promise<void>>>();
  readonly #metrics = new Map<string, Promise<void>>();
  readonly #edges = new Map<string, Set<string>>();
  readonly #views = new Set<Promise<void>>();
  active = true;
  constructor(readonly harness: TraceHarness, readonly trace: TraceContext, readonly loader: EvidenceLoader, readonly physicalSpanIds: readonly string[]) {}
  check(): void {
    if (!this.active) throw new Error("trace lease has ended; reacquire it from the dataset");
    this.loader.signal.throwIfAborted();
  }
  #node(node: Node): void {
    this.check();
    if (this.trace.view().by_id.get(node.node_id) !== node) throw new Error("node does not belong to this trace");
  }
  async dependencies(dependencies: readonly Dependency[], parent?: string): Promise<void> {
    await Promise.all(dependencies.map(dependency => {
      if (dependency instanceof EvidenceDependency) return this.loader.load(this.trace, dependency.span_ids, dependency.fields);
      const node = this.trace.view().by_id.get(dependency.node_id);
      if (!node) throw new Error(`unknown dependency node: ${dependency.node_id}`);
      return this.#fact(node, dependency.name, parent);
    }));
    this.check();
  }
  fact(node: Node, name: string): Promise<unknown> { return this.#fact(node, name); }
  async #fact(node: Node, name: string, parent?: string): Promise<unknown> {
    this.#node(node);
    const key = JSON.stringify([node.node_id, name]);
    if (parent) {
      const edges = this.#edges.get(parent) ?? new Set<string>();
      edges.add(key); this.#edges.set(parent, edges);
      const reaches = (current: string, seen = new Set<string>()): boolean => {
        if (current === parent) return true;
        if (seen.has(current)) return false;
        seen.add(current);
        return [...(this.#edges.get(current) ?? [])].some(child => reaches(child, seen));
      };
      if (reaches(key)) { edges.delete(key); throw new Error(`cyclic fact dependency: ${key}`); }
    }
    try {
      if (!this.#facts.has(key)) this.#facts.set(key, Promise.resolve().then(() => this.#compute(node, name, key)));
      return await this.#facts.get(key);
    } finally { if (parent) this.#edges.get(parent)?.delete(key); }
  }
  async #compute(node: Node, name: string, key: string): Promise<unknown> {
    this.check();
    const spec = this.trace.specs.get(node.kind)!;
    if (spec.detail_facts?.includes(name)) {
      await this.#once(node, spec, async () => {
        await this.loader.load(this.trace, node.span_ids, spec.detail_fields ?? []);
        this.check();
        const values = spec.build?.(this.trace.spans.get(node.primary_span_id)!,
          node.span_ids.filter(id => id !== node.primary_span_id).map(id => this.trace.spans.get(id)!)) ?? {};
        for (const field of spec.detail_facts!) if (Object.hasOwn(values, field)) node.facts[field] = values[field];
      });
      return node.facts[name];
    }
    if (Object.hasOwn(node.facts, name)) return node.facts[name];
    const producers = [HTTP_EVIDENCE, ...(this.harness.contributions.factProducers ?? [])]
      .filter(producer => producer.produces.includes(name) && producer.applies(node));
    const transforms = this.harness.transforms.filter(transform => transform.produces.includes(name) && transform.applies(node));
    if (producers.length + transforms.length > 1) throw new Error(`conflicting fact producers: ${node.kind}/${name}`);
    if (producers.length) {
      const producer = producers[0]!;
      // Resolve dependencies per requested output before joining the producer task, so cycles
      // through different outputs of the same producer are detected instead of deadlocking.
      await this.dependencies(producer.requires(node, this.trace), key);
      await this.#once(node, producer, async () => {
        this.check();
        const values = producer.compute(node, this.trace);
        if (Object.keys(values).some(field => !producer.produces.includes(field))) throw new Error(`undeclared fact outputs: ${name}`);
        if (Object.keys(values).some(field => Object.hasOwn(node.facts, field))) throw new Error(`conflicting fact write: ${name}`);
        Object.assign(node.facts, values);
      });
    } else if (transforms.length) {
      await this.dependencies(transforms[0]!.requires?.(node, this.trace) ?? [], key);
      this.trace.transforms!.materialize([[node, name]]);
    }
    return node.facts[name];
  }
  #once(node: Node, owner: object, work: () => Promise<void>): Promise<void> {
    const tasks = this.#producers.get(node) ?? new Map<object, Promise<void>>();
    this.#producers.set(node, tasks);
    if (!tasks.has(owner)) tasks.set(owner, Promise.resolve().then(work));
    return tasks.get(owner)!;
  }
  async metric(node: Node, name: string): Promise<Measurement | undefined> {
    this.#node(node);
    if (!this.#metrics.has(name)) this.#metrics.set(name, Promise.resolve().then(() => this.#measure(name)));
    await this.#metrics.get(name);
    this.check();
    return this.measurements.get(node.node_id, name);
  }
  async #measure(name: string): Promise<void> {
    const selected = this.harness.measurers.filter(item => item.spec.id === name);
    if (!selected.length) throw new Error(`unknown measurement: ${name}`);
    let result: Measurements;
    try {
      for (const item of selected) await this.dependencies(item.requires?.(this.trace) ?? []);
      this.check(); result = measure(this.trace, selected);
    } catch (error) {
      this.check();
      if (error instanceof EvidenceTooLarge) throw error;
      result = new Measurements(selected.map(item => item.spec), [], Object.fromEntries(this.trace.nodes.map(node => [node.node_id,
        [{ spec_id: name, anchor_node_id: node.node_id, status: "error", values: {}, evidence: {}, error: String(error) }]])));
    }
    this.measurements.specs.push(...result.specs);
    if (result.sources.length) this.measurements.sources.splice(0, this.measurements.sources.length, ...result.sources);
    for (const [id, values] of Object.entries(result.results)) (this.measurements.results[id] ??= []).push(...values);
    // Completion timing differs between cached and remote reads; output order must not.
    const order = new Map(this.harness.measurers.map((item, index) => [item.spec.id, index]));
    this.measurements.specs.sort((a, b) => order.get(a.id)! - order.get(b.id)!);
    for (const values of Object.values(this.measurements.results)) values.sort((a, b) => order.get(a.spec_id)! - order.get(b.spec_id)!);
  }
  prepareView(full = false): Promise<void> {
    const work = this.#prepareView(full);
    this.#views.add(work);
    void work.finally(() => this.#views.delete(work)).catch(() => {});
    return work;
  }
  async #prepareView(full: boolean): Promise<void> {
    this.check();
    if (full) await this.loader.load(this.trace,
      this.physicalSpanIds, null);
    for (const node of this.trace.nodes) {
      const spec = this.trace.specs.get(node.kind)!;
      await Promise.all([...new Set([...(spec.project_requires ?? []), ...(full ? spec.detail_facts ?? [] : [])])]
        .map(name => this.fact(node, name)));
      this.check();
      node.brief = spec.project?.(node) ?? [];
      if (node.has_error) node.error_text = spanErrorText(this.trace.spans.get(node.error_anchor));
    }
  }
  async close(): Promise<void> {
    this.active = false;
    await Promise.allSettled([...this.#facts.values(), ...this.#metrics.values(), ...this.#views]);
    this.#facts.clear(); this.#metrics.clear(); this.#producers.clear(); this.#edges.clear();
  }
}
