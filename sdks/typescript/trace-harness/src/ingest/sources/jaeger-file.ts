import { createReadStream } from "node:fs";
import { appendFile, mkdir, mkdtemp, opendir, readFile, rename, rm, stat } from "node:fs/promises";
import { tmpdir } from "node:os";
import { join, resolve } from "node:path";
import { createInterface } from "node:readline";
import { EvidenceStore, keyHash, cloneSpan } from "../../loading/store";
import { normalizeJaegerSpan } from "../jaeger";
import { EvidenceMissing, type EvidenceRef } from "../../loading/model";
import type { NormSpan } from "../../model/span";
import type { Source, SpanQuery } from "./base";

type Document = Record<string, unknown>;
async function* lines(path: string): AsyncIterable<string> {
  const stream = createReadStream(path, { encoding: "utf8" });
  const reader = createInterface({ input: stream, crlfDelay: Infinity });
  try { for await (const line of reader) if (line.trim()) yield line; }
  finally { reader.close(); stream.destroy(); }
}
function matches(span: NormSpan, query: SpanQuery): boolean {
  return (!query.error_only || span.has_error) && (!query.service || span.service === query.service)
    && (query.since_ms === undefined || span.start_ms >= query.since_ms)
    && (query.until_ms === undefined || span.start_ms < query.until_ms)
    && (!query.operation_names || query.operation_names.includes(span.name))
    && Object.entries(query.attr_eq ?? {}).every(([key, value]) => Object.hasOwn(span.attrs, key) && String(span.attrs[key]) === value);
}

/** Index JSONL one record at a time. UI JSON is parsed once, then released after indexing.
 * A retained indexDir allows reuse when the input file's identity has not changed.
 */
export class JaegerFileSource implements Source {
  readonly namespace: string;
  readonly path: string;
  #index?: Promise<string>;
  #closing?: Promise<void>;
  readonly #abort = new AbortController();
  constructor(path: string, readonly options: { format?: "jsonl" | "jaeger"; indexDir?: string } = {}) {
    this.path = resolve(path); this.namespace = `jaeger-file:${this.path}`;
  }
  #check(signal?: AbortSignal): void { this.#abort.signal.throwIfAborted(); signal?.throwIfAborted(); }
  #ensure(signal?: AbortSignal): Promise<string> {
    this.#check(signal);
    return this.#index ??= this.#build(signal);
  }
  async #build(signal?: AbortSignal): Promise<string> {
    const info = await stat(this.path);
    const signature = keyHash(JSON.stringify([this.path, info.size, info.mtimeMs, info.ino, this.options.format]));
    const target = this.options.indexDir ? join(this.options.indexDir, signature) : undefined;
    if (target && await new EvidenceStore(join(target, "spans")).get("complete")) return target;
    if (this.options.indexDir) await mkdir(this.options.indexDir, { recursive: true });
    const path = await mkdtemp(join(this.options.indexDir ?? tmpdir(), "trace-source-"));
    const store = new EvidenceStore(join(path, "spans"));
    await mkdir(join(path, "traces"), { recursive: true });
    const add = async (doc: Document) => {
      this.#check(signal);
      const span = normalizeJaegerSpan(doc);
      if (!span || typeof doc.traceID !== "string" || !doc.traceID) throw new Error("Jaeger record requires traceID and spanID");
      const key = JSON.stringify([doc.traceID, span.span_id]);
      if (await store.get(key)) throw new Error(`duplicate span in Jaeger input: ${doc.traceID}/${span.span_id}`);
      span.storage_index = signature;
      span.storage_id = keyHash(JSON.stringify(doc));
      await store.put(key, span);
      await appendFile(join(path, "traces", keyHash(doc.traceID)), JSON.stringify({ trace: doc.traceID, span: span.span_id }) + "\n");
    };
    try {
      if ((this.options.format ?? (this.path.endsWith(".jsonl") ? "jsonl" : "jaeger")) === "jsonl") {
        for await (const line of lines(this.path)) await add(JSON.parse(line));
      } else {
        const data = JSON.parse(await readFile(this.path, { encoding: "utf8", signal }));
        if (Array.isArray(data.data)) {
          for (const trace of data.data) for (const span of trace.spans ?? []) {
            await add({ ...span, traceID: span.traceID ?? trace.traceID, process: trace.processes?.[span.processID] ?? span.process });
          }
        } else if (Array.isArray(data)) { for (const doc of data) await add(doc); }
        else await add(data);
      }
      this.#check(signal);
      const after = await stat(this.path);
      if (after.size !== info.size || after.mtimeMs !== info.mtimeMs || after.ino !== info.ino) throw new Error("Jaeger file changed while indexing");
      await store.put("complete", true);
      if (target) {
        try { await rename(path, target); }
        catch (error) {
          if (!["EEXIST", "ENOTEMPTY"].includes((error as NodeJS.ErrnoException).code ?? "")
            || !await new EvidenceStore(join(target, "spans")).get("complete")) throw error;
          await rm(path, { recursive: true, force: true });
        }
      }
      return target ?? path;
    } catch (error) { await rm(path, { recursive: true, force: true }); throw error; }
  }
  async *select(query: SpanQuery, signal?: AbortSignal): AsyncIterable<string> {
    const path = await this.#ensure(signal);
    const store = new EvidenceStore(join(path, "spans"));
    const selected: Array<{ id: string; latest: number }> = [];
    const limit = query.limit ?? 1000;
    if (!Number.isSafeInteger(limit) || limit < 1) throw new Error("selection limit must be a positive integer");
    const compare = (a: { id: string; latest: number }, b: { id: string; latest: number }) =>
      (query.order === "latest" ? b.latest - a.latest : 0) || (a.id < b.id ? -1 : a.id > b.id ? 1 : 0);
    const dir = await opendir(join(path, "traces"));
    for await (const entry of dir) {
      this.#check(signal);
      let id = "", latest = -Infinity;
      for await (const line of lines(join(path, "traces", entry.name))) {
        this.#check(signal);
        const ref = JSON.parse(line); id = ref.trace;
        if (query.trace_ids && !query.trace_ids.includes(id)) break;
        const span = cloneSpan((await store.get<NormSpan>(JSON.stringify([id, ref.span])))!);
        if (matches(span, query)) latest = Math.max(latest, span.start_ms);
      }
      if (latest > -Infinity) {
        selected.push({ id, latest }); selected.sort(compare);
        if (selected.length > limit) selected.pop();
      }
    }
    for (const item of selected) { this.#check(signal); yield item.id; }
  }
  #project(span: NormSpan, fields: readonly string[] | null): NormSpan {
    const projected = cloneSpan(span);
    if (fields !== null) {
      for (const name of Object.keys(projected.attrs)) if (!fields.includes(name)) delete projected.attrs[name];
      projected.raw.tags = Object.entries(projected.attrs).map(([key, value]) => ({ key, value }));
    }
    projected.loaded_fields = fields;
    return projected;
  }
  async fetch(traceId: string, fields: readonly string[], signal?: AbortSignal): Promise<Map<string, NormSpan>> {
    const path = await this.#ensure(signal), store = new EvidenceStore(join(path, "spans"));
    const result = new Map<string, NormSpan>();
    try {
      for await (const line of lines(join(path, "traces", keyHash(traceId)))) {
        this.#check(signal);
        const ref = JSON.parse(line);
        const span = await store.get<NormSpan>(JSON.stringify([traceId, ref.span]));
        if (!span) throw new EvidenceMissing(`indexed evidence missing: ${traceId}/${ref.span}`);
        result.set(span.span_id, this.#project(span, fields));
      }
    } catch (error) { if ((error as NodeJS.ErrnoException).code !== "ENOENT") throw error; }
    return result;
  }
  async read(refs: readonly EvidenceRef[], fields: readonly string[] | null, signal?: AbortSignal): Promise<Map<string, NormSpan>> {
    const path = await this.#ensure(signal), store = new EvidenceStore(join(path, "spans"));
    const result = new Map<string, NormSpan>();
    for (const ref of refs) {
      this.#check(signal);
      const span = await store.get<NormSpan>(JSON.stringify([ref.trace_id, ref.span_id]));
      if (span && (!ref.index || ref.index === span.storage_index) && (!ref.document_id || ref.document_id === span.storage_id)) {
        result.set(ref.span_id, this.#project(span, fields));
      }
    }
    return result;
  }
  close(): Promise<void> {
    return this.#closing ??= (async () => {
      this.#abort.abort(new Error("Jaeger source is closed"));
      const path = await this.#index?.catch(() => undefined);
      if (path && !this.options.indexDir) await rm(path, { recursive: true, force: true });
    })();
  }
}
