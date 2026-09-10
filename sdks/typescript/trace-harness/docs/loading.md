# Managed trace loading

## Source, Dataset and Session

`TraceHarness.open(source, { workDir, config })` creates an execution scope. The Source exposes
`select(query, signal)`, `fetch(traceId, fields, signal)`, `read(refs, fields, signal)` and `close()`.
Selection returns unique IDs; fetch returns a complete projected skeleton; read extends evidence using
its frozen physical references. Remote adapters must propagate cancellation and use bounded client
concurrency/timeouts. The host supplies credentials and business query translation. No OpenSearch
adapter is supplied by this package; hosts can implement Source with their existing toolbox client.

`session.select()` persists a Dataset. `Dataset.load(path)` reopens it for the same Source namespace.
Source namespaces must identify backend/environment/index scope without embedding credentials.
The first skeleton fetch fixes observed span membership. New structural field requirements supplement
those references, without incorporating later spans. Create a new Dataset to refresh membership.

Use an individual trace when investigating one member:

```ts
const dataset = await Dataset.load(datasetPath);
await using session = harness.open(source, { workDir: workspace });
await using lease = await session.tree(dataset, traceId);
const analysis = await session.analyze(lease.analysis);
await session.prepareView(analysis, { full: true });
// Render and save the prepared result here.
```

`session.trees(dataset)` yields one active analysis at a time and releases it on advancement or break.
Separate `tree()` acquisitions may run concurrently, up to the active trace budget. A caller must close
its lease to admit queued traces. `close()` and asynchronous disposal are idempotent. Reusing a released
analysis runtime fails explicitly; acquire another lease to analyze again with fresh computation state.

## Declaring dependencies

Business classification declares `structureFields` on contributions and `structure_fields` on KindSpec.
`fieldAliases` maps stored tag names to canonical names; `normalizeSpan` handles protocol normalization,
and `prepareSpans` can perform domain mapping before assembly. Normalization must preserve physical
span identity and parentage. These hooks must be deterministic for cached evidence reuse.

Fact producers declare their input evidence and perform pure computation after loading:

```ts
const producer: FactProducer = {
  produces: ["request_size"],
  applies: node => node.kind === "model-call",
  requires: node => [new EvidenceDependency(node.span_ids, ["request.body"])],
  compute: (node, trace) => ({
    request_size: String(trace.raw_attr(node.primary_span_id)["request.body"] ?? "").length,
  }),
};
// Register through TraceContributions.factProducers.
const value = await analysis.fact(node, "request_size");
```

`FactDependency(nodeId, name)` prepares another fact. FactTransform and Measurer can declare `requires`
using these same dependencies; their compute functions remain synchronous. Async detector handlers
can await `context.fact(node, name)` and `context.measure(node, metricId)`. Computing a metric once
prepares its results for all nodes in that lease. Missing metric evidence becomes an explicit error
result; unknown metrics and resource budget failures reject.

A KindSpec may declare `detail_fields` and `detail_facts` to reuse its pure builder for details.
Fetching them never repeats classification or changes node parents. `project_requires` lists facts
needed by brief projection. Only explicit `prepareView()` prepares brief fields; `{ full: true }`
additionally loads complete evidence and declared detail facts. Renderers consume prepared results.

## Loading policy and limits

`lazy` defaults to true. With `lazy: false`, `fields` selects the active trace's preload fields;
null or omission requests full evidence, while an empty array requests nothing extra. An unused
preload failure does not invalidate unrelated analysis. A later consumer must successfully obtain
the field or receive the read error. Both modes enforce evidence byte budgets.

`concurrency` bounds Source I/O across all Datasets in a Session. `activeTraces` bounds live leases.
Decoded evidence per trace is limited by the smaller of `maxTraceBytes` and `cacheBytes / activeTraces`.
`session.loadingStats` exposes fetch/read counts, bytes and cache hits for the whole Session.
These limits do not bound arbitrary user-computed facts, parser transient allocations or HTML strings.

The TS implementation stores Dataset membership and evidence as files, atomically replacing hashed
cache entries. It does not retain an entire Dataset of trees or decoded evidence in memory. Cache
storage is private to this SDK; it is not the Python SQLite layout. Evidence may be reused across
runs, but computation caches belong only to one lease.

`JaegerFileSource` supports JSONL, Jaeger UI JSON and JSON arrays. JSONL indexing holds one record at a
time; UI JSON and arrays are parsed as a whole before indexing. Full records are retained on disk,
then field projections are read for analysis. An `indexDir` retains imports for an unchanged file;
otherwise Source.close removes its temporary index. Concurrent imports publish complete indexes
atomically. Use JSONL for large inputs. A record or trace exceeding runtime budgets still fails explicitly.

Omitted workDir creates a temporary Session workspace, removed at close. A supplied workDir preserves
Datasets and evidence. Save reports outside temporary workspaces when they must survive Session close.

The language-neutral requirements and shared fixture are in [the loading contract](../../../../spec/trace-loading.md).
