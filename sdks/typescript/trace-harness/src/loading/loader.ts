import { ConcurrencyPool } from "@compforge/harness-toolbox/concurrency";
import type { Source } from "../ingest/sources/base";
import type { TraceContext } from "../model/context";
import type { NormSpan } from "../model/span";
import { EvidenceMissing, EvidenceTooLarge, type LoadConfig } from "./model";
import { cloneSpan, EvidenceStore } from "./store";

type Fields = readonly string[] | null;
interface Request {
  trace: TraceContext; ids: readonly string[]; fields: Fields;
  resolve(): void; reject(error: unknown): void;
}
const covers = (span: NormSpan, fields: Fields): boolean => span.loaded_fields === null
  || fields !== null && fields.every(field => span.loaded_fields!.includes(field));
const union = (a: Fields, b: Fields): Fields => a === null || b === null ? null : [...new Set([...a, ...b])];

/** @spec Evidence is cached independently of computations; detail reads never change topology.
 * @why All leases of a trace coordinate through one loader, but receive separate mutable spans.
 */
export class EvidenceLoader {
  readonly stats = { reads: 0, fetches: 0, bytesRead: 0, cacheHits: 0 };
  readonly #pending = new Map<string, Request[]>();
  readonly #locks = new Map<string, Promise<unknown>>();
  constructor(readonly source: Source, readonly store: EvidenceStore,
    readonly config: Required<LoadConfig>, readonly pool: ConcurrencyPool, readonly signal: AbortSignal,
    readonly aliases: Readonly<Record<string, string>> = {},
    readonly normalize?: (span: NormSpan) => NormSpan) {}

  #serial<T>(key: string, work: () => Promise<T>): Promise<T> {
    const previous = this.#locks.get(key) ?? Promise.resolve();
    const next = previous.catch(() => {}).then(() => { this.signal.throwIfAborted(); return work(); });
    this.#locks.set(key, next);
    void next.finally(() => { if (this.#locks.get(key) === next) this.#locks.delete(key); }).catch(() => {});
    return next;
  }
  #fields(fields: Fields): Fields {
    return fields === null ? null : [...new Set([...fields,
      ...Object.entries(this.aliases).filter(([, canonical]) => fields.includes(canonical)).map(([alias]) => alias)])];
  }
  #normalized(span: NormSpan): NormSpan {
    const original = cloneSpan(span);
    for (const [alias, canonical] of Object.entries(this.aliases)) {
      if (!Object.hasOwn(original.attrs, canonical) && Object.hasOwn(original.attrs, alias)) {
        original.attrs[canonical] = original.attrs[alias];
      }
    }
    const result = this.normalize?.(original) ?? original;
    if (result.span_id !== span.span_id || result.parent_span_id !== span.parent_span_id) {
      throw new Error("normalization must preserve physical span identity and parent");
    }
    return result;
  }
  checkSize(spans: Map<string, NormSpan>): number {
    const size = [...spans.values()].reduce((sum, span) => sum + Buffer.byteLength(JSON.stringify(span)), 0);
    if (size > Math.min(this.config.maxTraceBytes, Math.floor(this.config.cacheBytes / this.config.activeTraces))) {
      throw new EvidenceTooLarge(`trace evidence requires ${size} bytes, exceeding loading budget`);
    }
    return size;
  }
  async skeleton(traceId: string, fields: readonly string[]): Promise<Map<string, NormSpan>> {
    return this.#serial(traceId, async () => {
      const key = `skeleton:${traceId}`;
      const saved = await this.store.get<Array<[string, NormSpan]>>(key);
      let spans: Map<string, NormSpan>;
      if (saved) {
        spans = new Map(saved.map(([id, span]) => [id, cloneSpan(span)]));
        // Extend projection through frozen identities, never fetch a new span set.
        await this.#read(traceId, spans, fields);
        this.stats.cacheHits++;
      } else {
        spans = await this.pool.run(() => this.source.fetch(traceId, this.#fields(fields)!, this.signal), this.signal);
        this.signal.throwIfAborted();
        if (!spans.size) throw new EvidenceMissing(`trace has no available spans: ${traceId}`);
        spans = new Map([...spans].map(([id, span]) => {
          if (id !== span.span_id) throw new Error(`invalid skeleton identity: ${traceId}/${id}`);
          const normalized = this.#normalized(span);
          normalized.loaded_fields = fields;
          return [id, normalized];
        }));
        this.stats.bytesRead += this.checkSize(spans);
        this.stats.fetches++;
      }
      this.checkSize(spans);
      await this.store.put(key, [...spans]);
      for (const [id, span] of spans) await this.#save(traceId, id, span);
      return spans;
    });
  }
  load(trace: TraceContext, ids: readonly string[], fields: Fields): Promise<void> {
    this.signal.throwIfAborted();
    for (const id of ids) if (!trace.spans.has(id)) throw new EvidenceMissing(`span absent from frozen skeleton: ${trace.trace_id}/${id}`);
    if (ids.every(id => covers(trace.spans.get(id)!, fields))) return Promise.resolve();
    return new Promise((resolve, reject) => {
      const requests = this.#pending.get(trace.trace_id) ?? [];
      requests.push({ trace, ids, fields, resolve, reject });
      this.#pending.set(trace.trace_id, requests);
      if (requests.length === 1) {
        // A microtask merges concurrent consumers without an arbitrary timer delay.
        queueMicrotask(() => {
          const batch = this.#pending.get(trace.trace_id)!;
          this.#pending.delete(trace.trace_id);
          void this.#serial(trace.trace_id, () => this.#flush(batch)).catch(error => {
            for (const request of batch) request.reject(error);
          });
        });
      }
    });
  }
  async #flush(requests: Request[]): Promise<void> {
    const fields = requests.reduce<Fields>((value, request) => union(value, request.fields), []);
    const spans = new Map<string, NormSpan>();
    for (const request of requests) for (const id of request.ids) spans.set(id, cloneSpan(request.trace.spans.get(id)!));
    try {
      await this.#read(requests[0]!.trace.trace_id, spans, fields);
      for (const request of requests) {
        const staged = new Map(request.trace.spans);
        for (const id of request.ids) staged.set(id, this.#merge(staged.get(id)!, spans.get(id)!, request.fields));
        this.checkSize(staged);
        this.signal.throwIfAborted();
        for (const id of request.ids) Object.assign(request.trace.spans.get(id)!, staged.get(id)!);
        request.resolve();
      }
    } catch (error) {
      for (const request of requests) {
        for (const id of request.ids) for (const name of request.fields ?? ["*"]) {
          request.trace.spans.get(id)!.field_errors[name] = error instanceof Error ? error.message : String(error);
        }
        request.reject(error);
      }
    }
  }
  #merge(target: NormSpan, incoming: NormSpan, fields: Fields): NormSpan {
    if (target.span_id !== incoming.span_id || target.parent_span_id !== incoming.parent_span_id
      || target.name !== incoming.name || target.start_ms !== incoming.start_ms || target.dur_ms !== incoming.dur_ms
      || target.service !== incoming.service || target.has_error !== incoming.has_error
      || target.storage_id !== incoming.storage_id || target.storage_index !== incoming.storage_index) {
      throw new EvidenceMissing(`evidence identity changed: ${target.span_id}`);
    }
    const merged = cloneSpan(target);
    const names = fields ?? Object.keys(incoming.attrs);
    for (const name of names) if (Object.hasOwn(incoming.attrs, name)) merged.attrs[name] = incoming.attrs[name];
    // Keep structural values from the skeleton. Only evidence payload is extended.
    // Raw documents are opaque Source snapshots. Do not merge backend-specific tag formats.
    // Once a full snapshot is cached, a later projection must not replace it with partial raw.
    merged.raw = target.loaded_fields === null && fields !== null ? merged.raw : structuredClone(incoming.raw);
    if (fields === null) { merged.events = incoming.events; merged.error_events = incoming.error_events; }
    merged.loaded_fields = union(target.loaded_fields, fields);
    for (const name of fields ?? ["*"]) delete merged.field_errors[name];
    return merged;
  }
  async #read(traceId: string, spans: Map<string, NormSpan>, fields: Fields): Promise<void> {
    const missing: string[] = [];
    for (const [id, span] of spans) {
      if (covers(span, fields)) continue;
      const cached = await this.store.get<NormSpan>(JSON.stringify(["span", traceId, id]));
      if (cached && covers(cached, fields)) { spans.set(id, this.#merge(span, cached, fields)); this.stats.cacheHits++; }
      else missing.push(id);
    }
    for (let offset = 0; offset < missing.length; offset += 100) {
      const ids = missing.slice(offset, offset + 100);
      const refs = ids.map(id => ({ trace_id: traceId, span_id: id,
        index: spans.get(id)!.storage_index, document_id: spans.get(id)!.storage_id }));
      const fetched = await this.pool.run(() => this.source.read(refs, this.#fields(fields), this.signal), this.signal);
      this.signal.throwIfAborted();
      this.stats.reads++;
      this.stats.bytesRead += this.checkSize(fetched);
      for (const id of ids) {
        const incoming = fetched.get(id);
        if (!incoming) throw new EvidenceMissing(`evidence expired or missing: ${traceId}/${id}`);
        spans.set(id, this.#merge(spans.get(id)!, this.#normalized(incoming), fields));
      }
    }
    this.checkSize(spans);
    // Failed reads never commit a partially prepared set as successful evidence.
    for (const id of missing) await this.#save(traceId, id, spans.get(id)!);
  }
  async #save(traceId: string, id: string, span: NormSpan): Promise<void> {
    const key = JSON.stringify(["span", traceId, id]);
    const cached = await this.store.get<NormSpan>(key);
    await this.store.put(key, cached ? this.#merge(cached, span, span.loaded_fields) : span);
  }
  async close(): Promise<void> {
    // Session aborts first, preventing queued work from reaching the Source.
    await Promise.resolve();
    await Promise.allSettled([...this.#locks.values()]);
  }
}
