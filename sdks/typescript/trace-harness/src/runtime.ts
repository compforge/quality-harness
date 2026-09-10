import { ConcurrencyPool } from "@compforge/harness-toolbox/concurrency";
import { randomUUID } from "node:crypto";
import { mkdir, mkdtemp, open, rm, writeFile } from "node:fs/promises";
import { tmpdir } from "node:os";
import { join } from "node:path";
import type { TraceHarness } from "./harness";
import type { Source, SpanQuery } from "./ingest/sources/base";
import { Dataset } from "./dataset";
import { AnalysisContext } from "./analyze/context";
import { diagnoseAnalysis } from "./analyze/diagnose";
import { assemble } from "./ingest/assemble";
import { TraceAnalysis } from "./loading/analysis";
import { EvidenceLoader } from "./loading/loader";
import { EvidenceStore } from "./loading/store";
import { EvidenceTooLarge, loadConfig, type LoadConfig } from "./loading/model";
import { STRUCTURE_FIELDS } from "./loading/structure";

export interface SessionOptions { workDir?: string; config?: LoadConfig }
function deferred<T>() {
  let resolve!: (value: T) => void, reject!: (error: unknown) => void;
  const promise = new Promise<T>((yes, no) => { resolve = yes; reject = no; });
  return { promise, resolve, reject };
}

/** Use `await using lease = await session.tree(...)` or close in finally.
 * The analysis is valid only while its lease is active.
 */
export class TraceLease {
  #closing?: Promise<void>;
  constructor(readonly analysis: AnalysisContext, private readonly release: () => void) {}
  close(): Promise<void> {
    return this.#closing ??= this.analysis.runtime!.close().finally(this.release);
  }
  [Symbol.asyncDispose](): Promise<void> { return this.close(); }
}

/** @spec A session owns Source lifetime and aggregate reading/active-trace budgets.
 * @why Dataset caches survive individual leases; analysis tasks never survive a lease.
 */
export class TraceSession {
  readonly config: Required<LoadConfig>;
  readonly #abort = new AbortController();
  readonly #reads: ConcurrencyPool;
  readonly #slots: ConcurrencyPool;
  readonly #loaders = new Map<string, EvidenceLoader>();
  readonly #leases = new Set<TraceLease>();
  readonly #operations = new Set<Promise<unknown>>();
  #workDir?: Promise<string>;
  #closing?: Promise<void>;
  constructor(readonly harness: TraceHarness, readonly source: Source, readonly options: SessionOptions = {}) {
    this.config = loadConfig(options.config);
    this.#reads = new ConcurrencyPool(this.config.concurrency);
    this.#slots = new ConcurrencyPool(this.config.activeTraces);
    const ids = harness.measurers.map(item => item.spec.id);
    if (new Set(ids).size !== ids.length) throw new Error("duplicate measurement id");
  }
  #check(): void { this.#abort.signal.throwIfAborted(); }
  #track<T>(work: Promise<T>): Promise<T> {
    this.#operations.add(work);
    void work.finally(() => this.#operations.delete(work)).catch(() => {});
    return work;
  }
  workspace(): Promise<string> {
    this.#check();
    return this.#workDir ??= this.options.workDir
      ? mkdir(this.options.workDir, { recursive: true }).then(() => this.options.workDir!)
      : mkdtemp(join(tmpdir(), "trace-harness-"));
  }
  select(query: SpanQuery = {}): Promise<Dataset> {
    this.#check();
    return this.#track(this.#select(query));
  }
  async #select(query: SpanQuery): Promise<Dataset> {
    const limit = query.limit ?? 1000;
    if (!Number.isSafeInteger(limit) || limit < 1) throw new Error("selection limit must be a positive integer");
    const id = randomUUID(), path = join(await this.workspace(), "datasets", id);
    await mkdir(path, { recursive: true });
    const members = new EvidenceStore(join(path, "members"));
    const stream = await open(join(path, "members.jsonl"), "wx");
    let count = 0;
    try {
      // Selection uses the same Source I/O budget, and never acquires trees.
      await this.#reads.run(async () => {
        for await (const traceId of this.source.select({ ...query, limit }, this.#abort.signal)) {
          this.#check();
          if (!traceId || typeof traceId !== "string") throw new Error("Source returned an empty trace identity");
          if (await members.get(traceId)) continue;
          await members.put(traceId, traceId);
          await stream.write(`${JSON.stringify(traceId)}\n`);
          if (++count >= limit) break;
        }
      }, this.#abort.signal);
      this.#check();
      await stream.close();
      await writeFile(join(path, "manifest.json"), JSON.stringify({ id, source: this.source.namespace, count, query, limit_reached: count === limit }));
      return new Dataset(id, this.source.namespace, path, count);
    } catch (error) { await stream.close(); await rm(path, { recursive: true, force: true }); throw error; }
  }
  #loader(dataset: Dataset): EvidenceLoader {
    this.#check();
    if (dataset.source !== this.source.namespace) throw new Error("dataset belongs to a different source");
    const key = dataset.path;
    if (!this.#loaders.has(key)) this.#loaders.set(key, new EvidenceLoader(this.source,
      new EvidenceStore(join(dataset.path, "evidence-cache")), this.config, this.#reads, this.#abort.signal,
      this.harness.contributions.fieldAliases, this.harness.contributions.normalizeSpan));
    return this.#loaders.get(key)!;
  }
  tree(dataset: Dataset, traceId: string): Promise<TraceLease> {
    this.#check();
    const ready = deferred<TraceLease>(), released = deferred<void>();
    const operation = this.#slots.run(async () => {
      if (!await dataset.contains(traceId)) throw new Error(`trace is not a member of dataset: ${traceId}`);
      const loader = this.#loader(dataset);
      const fields = [...new Set([...STRUCTURE_FIELDS, ...(this.harness.contributions.structureFields ?? []),
        ...[...this.harness.specs].flatMap(spec => spec.structure_fields ?? [])])];
      const skeleton = await loader.skeleton(traceId, fields);
      const physicalSpanIds = [...skeleton.keys()];
      const spans = this.harness.contributions.prepareSpans?.(skeleton) ?? skeleton;
      const trace = assemble(spans, this.harness.specs, this.harness.transforms, false, traceId);
      const runtime = new TraceAnalysis(this.harness, trace, loader, physicalSpanIds);
      const analysis = new AnalysisContext(trace, runtime.measurements, {}, runtime);
      const lease = new TraceLease(analysis, () => { this.#leases.delete(lease); released.resolve(); });
      this.#leases.add(lease);
      try {
        if (!this.config.lazy) {
          try { await loader.load(trace, physicalSpanIds, this.config.fields); }
          catch (error) {
            this.#check();
            // A failed unused field does not invalidate independent analysis; budgets always do.
            if (error instanceof EvidenceTooLarge) throw error;
          }
        }
        this.#check(); ready.resolve(lease);
        await released.promise;
      } catch (error) { await lease.close(); throw error; }
    }, this.#abort.signal);
    void this.#track(operation).catch(ready.reject);
    return ready.promise;
  }
  async *trees(dataset: Dataset): AsyncIterable<AnalysisContext> {
    for await (const id of dataset.members()) {
      const lease = await this.tree(dataset, id);
      try { yield lease.analysis; } finally { await lease.close(); }
    }
  }
  async analyze(analysis: AnalysisContext, options: { metrics?: readonly string[]; diagnosis?: boolean } = {}): Promise<AnalysisContext> {
    this.#runtime(analysis);
    const names = options.metrics ?? this.harness.measurers.map(item => item.spec.id);
    for (const name of names) if (!this.harness.measurers.some(item => item.spec.id === name)) throw new Error(`unknown measurement: ${name}`);
    const anchor = analysis.trace.nodes[0];
    if (anchor) await Promise.all(names.map(name => analysis.measure(anchor, name)));
    if (options.diagnosis === false) return analysis;
    await Promise.all(analysis.trace.nodes.flatMap(node => Object.keys(analysis.trace.specs.get(node.kind)?.metrics ?? {})
      .map(name => analysis.fact(node, name))));
    return diagnoseAnalysis(analysis, this.harness.detectors);
  }
  async prepareView(analysis: AnalysisContext, options: { full?: boolean } = {}): Promise<AnalysisContext> {
    await this.#runtime(analysis).prepareView(options.full); return analysis;
  }
  #runtime(analysis: AnalysisContext): TraceAnalysis {
    this.#check();
    if (!analysis.runtime || ![...this.#leases].some(lease => lease.analysis.runtime === analysis.runtime)) throw new Error("analysis does not belong to an active lease of this session");
    analysis.runtime.check(); return analysis.runtime;
  }
  get loadingStats() {
    return [...this.#loaders.values()].reduce((total, loader) => ({
      reads: total.reads + loader.stats.reads, fetches: total.fetches + loader.stats.fetches,
      bytesRead: total.bytesRead + loader.stats.bytesRead, cacheHits: total.cacheHits + loader.stats.cacheHits,
    }), { reads: 0, fetches: 0, bytesRead: 0, cacheHits: 0 });
  }
  close(): Promise<void> { return this.#closing ??= this.#close(); }
  async #close(): Promise<void> {
    this.#abort.abort(new Error("trace session is closed"));
    try {
      await Promise.allSettled([...this.#leases].map(lease => lease.close()));
      await Promise.allSettled([...this.#operations]);
      await Promise.allSettled([...this.#loaders.values()].map(loader => loader.close()));
      await this.source.close();
    } finally {
      this.#loaders.clear();
      if (!this.options.workDir && this.#workDir) await rm(await this.#workDir, { recursive: true, force: true });
    }
  }
  [Symbol.asyncDispose](): Promise<void> { return this.close(); }
}
