import { readFileSync } from "node:fs";
import { normalizeJaegerSpan, TraceHarness, EvidenceDependency, genAiSpecs,
  type Source, type SpanQuery, type EvidenceRef, type TraceContributions, type NormSpan } from "../src/index";
export const fixture = JSON.parse(readFileSync(new URL("../../../../conformance/trace/loading.json", import.meta.url), "utf8"));
export class MemorySource implements Source {
  namespace = "loading-test";
  docs: Record<string, Record<string, unknown>> = Object.fromEntries(fixture.docs.map((doc: Record<string, unknown>) => [doc.traceID, structuredClone(doc)]));
  reads = 0; fetches = 0; closes = 0;
  async *select(query: SpanQuery, signal?: AbortSignal) {
    for (const id of Object.keys(this.docs).sort().slice(0, query.limit)) {
      signal?.throwIfAborted(); if (!query.trace_ids || query.trace_ids.includes(id)) yield id;
    }
  }
  project(doc: Record<string, unknown>, fields: readonly string[] | null): NormSpan {
    return normalizeJaegerSpan({ ...doc, tags: (doc.tags as Array<{key: string}>).filter(tag => fields === null || fields.includes(tag.key)) })!;
  }
  async fetch(id: string, fields: readonly string[], signal?: AbortSignal) {
    signal?.throwIfAborted(); this.fetches++;
    return this.docs[id] ? new Map([["s", this.project(this.docs[id]!, fields)]]) : new Map<string, NormSpan>();
  }
  async read(refs: readonly EvidenceRef[], fields: readonly string[] | null, signal?: AbortSignal) {
    signal?.throwIfAborted(); this.reads++; await Promise.resolve();
    return new Map(refs.filter(ref => this.docs[ref.trace_id]).map(ref => [ref.span_id, this.project(this.docs[ref.trace_id]!, fields)]));
  }
  async close() { this.closes++; }
}
export function harness(extra: TraceContributions = {}) {
  return new TraceHarness({ specs: genAiSpecs(), factProducers: [{ produces: ["size"], applies: () => true,
    requires: node => [new EvidenceDependency([node.primary_span_id], ["payload"])],
    compute: (node, trace) => ({ size: String(trace.raw_attr(node.primary_span_id).payload).length }) }], ...extra });
}
export function deferred() {
  let resolve!: () => void;
  const promise = new Promise<void>(yes => { resolve = yes; });
  return { promise, resolve };
}
