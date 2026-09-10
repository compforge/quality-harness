# Trace Harness 2.0

## 1. Scope

Trace Harness defines a language-neutral pipeline for turning raw distributed-trace spans into
logical analysis nodes, measurements, findings, and presentation trees. Python and TypeScript are peer
implementations of this specification. Neither implementation is the specification.

The key words **MUST**, **MUST NOT**, **SHOULD**, **SHOULD NOT**, and **MAY** describe normative
requirements.

Collection, environment discovery, credentials, ID resolution, and delivery of evidence bundles
belong to the host. A Trace Harness starts from raw span documents or normalized spans.

In the quality-harness kernel vocabulary, an assembled `Node` is an Observation and
`trace_id + node_id` identifies a node-grain Unit. A nodes/corpus collection is the reusable
Dataset; each EvaluationRun records the selected detector and gate configuration; diagnosis fills
a run-scoped Worksheet with Findings returned by detection. A trace- or cohort-grain analysis MUST use a distinct Unit
grain rather than mixing row meanings. These semantic mappings do not require peer
implementations to share a Worksheet class or storage layout.

## 2. Pipeline

One configured harness executes these stages in order:

```text
raw spans -> normalize -> assemble (build -> transform requested facts -> project brief)
                              -> measure -> diagnose -> render
                              -> transform requested facts -> render
                              -> NodeTreeExtractor -> AgentRun IR -> render
```

1. `normalize` maps backend-specific documents to `NormSpan` without assigning semantic kinds.
2. `assemble` applies ordered `KindSpec` values, fuses spans into logical nodes and creates parent
   edges. `KindSpec.build` owns raw protocol extraction. `FactTransform` computes additional
   standardized facts from modeled nodes and relationships before brief projection.
3. `measure` runs each configured measurer **once per trace**, producing independent descriptive
   results. It MUST NOT mutate node facts or imply a diagnostic judgment.
4. `diagnose` emits Findings. Physical errors and per-kind rules precede post-order scoped
   detectors. Both rules and detectors receive `(node, analysis_context)`. The context exposes
   the original trace, current Measurements and previously emitted Findings. Only the diagnosis
   engine appends Findings. Analysis results and caches MUST be isolated between runs.
5. `render` consumes prepared node facts, Measurements and Findings. It MUST
   NOT execute measurers, detectors or transforms. Facets declare presentation intent;
   the harness owns traversal, display-tree construction and serialization. Perspective and
   layout remain orthogonal, and synthetic presentation groups MUST NOT become call observations.
6. A consumer MAY request additional facts such as curl text or messages through `transform`.
   These are ordinary named facts and MUST be materialized into `node.facts` before rendering.
   Transform computation MUST NOT execute remote requests or interpret raw protocols.
7. `agent_run_extractor` MAY read the complete trace, apply framework-specific correlation and
   emit AgentRun IR. The harness validates and renders the result.
8. A probe MAY write evidence only when explicitly enabled at the call site. Import, construction,
   assembly, measurement, ordinary diagnosis, inspection and rendering MUST NOT write evidence.

`analyze` composes `measure → diagnose` and returns an analysis context. Measurement MUST also
be usable without diagnosis. Repeated analyses of the same trace MUST NOT append to earlier runs.

Only `assemble` may create or change logical node parent edges. Later stages MUST be
structure-preserving. A coarse perspective MAY replace an uninteresting display path with a
synthetic context connector, but it MUST retain the represented node IDs and MUST NOT rewrite
`Node.parent_node_id`.

## 3. Analysis IR

The canonical JSON projection is `trace-harness/analysis@2`, defined by
[`analysis.schema.json`](../schema/trace/v2/analysis.schema.json).

- `NormSpan` is one normalized physical span and retains raw data for provenance.
- `Node` is the node-grain Unit Observation: one logical operation backed by one or more physical spans.
- `facts` contains standardized JSON-compatible observations produced by build and fact transformation.
- `Measurement` describes a computed quantity with identity, scope, anchor, units, dimensions,
  values and evidence selection. It carries no severity or verdict.
- `measurements` stores specs, a shared call source index and results grouped by anchor node ID.
  Every result carries `measured`, `not_applicable` or `error`; unmeasured results MUST have empty
  values, and failures MUST NOT be represented as zero.
- `Finding` is the diagnostic output of detection attached to a node, trace, or cohort. A finding is not a verdict.
- `brief` is the baked, language-neutral field projection used by renderers.

Implementations MUST order nodes by `(start_ms, node_id)` and findings by
`(scope, ref, source, severity, note)` when producing the canonical JSON projection. Runtime
collections do not otherwise need to use this order.

### Fact transformation

`FactTransform(produces, applies, compute)` transforms existing facts into new named facts.
The same contract handles `HTTP statuses → http_status` and `request → curl`. Transform types
MUST NOT classify outputs as facts versus details, or declare eager/lazy/bake scheduling flags.
`KindSpec.build` owns protocol extraction; the transform context exposes modeled relationships
and `get(node, name)` for fact dependencies, with no raw span access.

Consumers select the output names they need. `KindSpec.project_requires` requests facts before
brief projection. Hosts use `harness.transform(node, trace, *names)` for selected node facts or
`transform_all(trace, *names)` before analysis/rendering (TypeScript: `transformAll`). Merely
registering a transform MUST NOT run it. Renderers MUST NOT initiate transformations.

One assembled trace owns its `TransformContext`. Dependencies are memoized across requests in
that trace, including all outputs of one producer and omitted outputs. Contexts MUST NOT share
node caches between traces. Base facts and topology are immutable inputs after construction;
only explicit materialization adds facts. Cycles, undeclared outputs, multiple applicable
producers of a name and collisions with base facts MUST fail explicitly. A materialization
batch commits all dependency outputs atomically; failure MUST leave neither partial facts nor
cached partial computations, so a later request can retry.

`http_status` and `curl` are facts; `self_ms` is a Measurement. Persisted facts remain renderable
without transforms or raw spans. Long strings and structured values MAY use expandable fact
presentation; display policy does not alter their role as facts.

### Measurement scopes and evidence

A spec declares `id`, `scope`, `units` by value key, `description` and `dimensions`. A result
references `spec_id` and `anchor_node_id`, then provides `status`, `values`, `evidence` and `error`.
Measurement has the same descriptive role across trace and trajectory, but each domain owns
its types, observation model and algorithms; sharing a runtime class is not required.

`self_ms` has node scope. It is the node duration minus the union of direct-child intervals,
clipped to the node interval. A leaf therefore reports its full duration.

`calls_until_node_end` has trace-prefix scope and a `kind` dimension:

- The window starts at the earliest observed span/node start in the current trace and ends at
  the anchor node's end. It includes siblings and other roots, not only the anchor's subtree.
- Count every logical call whose start is **at or before** the cutoff. Calls still in flight
  contribute their elapsed duration through the cutoff. A call starting exactly at the cutoff
  counts once with zero elapsed duration.
- Each kind reports `count` (calls), `duration_sum_ms` (clipped interval sum) and `covered_ms`
  (clipped interval union). Zero is a measured value; no applicable result and failure are distinct.
- Count genuine modeled operations even if they have children. Exclude coarse residual `service`
  nodes and presentation groups. Count direct HTTP client/server pairs once using caller timing;
  retain both span references in the source index. Include model HTTP and streaming requests.
- Kinds overlap: neither duration sums nor kind coverage may be summed as total wall-clock time
  or presented as a causal contribution to latency.
- Build the call index once and sweep time events per kind. Implementations MUST NOT rescan
  all previous calls for every node or repeat cumulative evidence arrays in each result.
  `evidence={source:"calls", start_ms, end_ms}` selects the shared `sources` records by start
  through the cutoff; kind selects a dimensional row. Durations are clipped to that window.
  `self_ms` uses `{source:"direct_children", node_id}` to select from the persisted node table.

A subtree cumulative measurement would have a different scope; it is not defined by this version.

The analysis artifact MUST retain measured results independently of facts and Findings. Offline
reload MUST render persisted results without recomputation. Interactive node details show scope,
kind, counts, duration sum and coverage even with no Finding; Markdown exposes the same values.

## 4. AgentRun IR

The canonical concern-specific projection is `trace-harness/agent-run@1`, defined by
[`agent-run.schema.json`](../schema/trace/v1/agent-run.schema.json).

```text
AgentRun.items
  ├─ Operation ─operations─→ Operation*
  │             └agent_runs─→ AgentRun*
  └─ AgentTurn.items
       ├─ ModelCall
       ├─ ToolCall ─agent_runs─→ AgentRun*
       └─ Operation ─operations─→ Operation*
                    └agent_runs─→ AgentRun*
```

- `AgentRun` is one agent execution extracted from a broader node tree.
- `AgentRun.items` preserves the order of turns and run-level operations before, between, or after
  the agent loop.
- `AgentTurn.items` preserves the order of model calls, tool calls, and turn-level operations.
- `ModelCall` and `ToolCall` carry their structured input and output.
- `Operation` represents known non-call work such as initialization, context compaction, wrap-up,
  or finalization, and unknown framework extensions without forcing them into model or tool
  semantics. It MAY belong directly to an `AgentRun`, to an `AgentTurn`, or recursively to another
  `Operation` through ordered `operations`.
- A `ToolCall` or `Operation` MAY contain ordered `agent_runs`. The call site and nested execution
  remain distinct: the parent keeps invocation input/output while each nested `AgentRun` keeps its
  own turns and operations. An opaque invocation uses an empty `agent_runs` collection.
- `source_node_ids` preserve provenance and enable drill-down to the source node and spans.

`NodeTreeExtractor<T>` is the deterministic transformation boundary from the complete in-memory
node tree to one concern-specific IR. A domain package MAY contribute a
`NodeTreeExtractor<AgentRunIR>`. Extractors own run and turn boundaries, model/tool correlation,
operation naming, and any reconstruction from framework events. The harness owns IR validation,
serialization, and rendering.

AgentRun IDs, turn IDs, and operation/call IDs MUST be unique across one AgentRun IR. Runs,
run items, turn items, nested `operations`, and nested `agent_runs` MUST be ordered by `start_ms`;
durations MUST be non-negative; each parent time window MUST contain its child items and nested
runs; every `source_node_ids` entry MUST reference a node in the source tree.

## 5. Scoped composition

`TraceHarness` is the state owner for one executable analysis configuration. It is reusable across
traces and MUST isolate its configuration from every other harness instance.

`TraceContributions` is the extension boundary used by a domain package or Plugin. It contains
these explicit extension slots:

| Slot | Consumed by | Ordering rule |
| --- | --- | --- |
| `specs` | assemble and per-kind diagnose | first matching spec wins |
| `transforms` | facts requested by projection, analysis or inspection | dependencies via pull/memo; conflicts fail |
| `measurers` | whole-trace measurement | each runs once; spec IDs must be unique |
| `detectors` | diagnose | declaration order within each post-order node |
| `facets` | render | highest priority wins; declaration order breaks ties; an undefined perspective level falls through |
| `measurement_filter` | report rows only | first contributed predicate wins; absent means show all |
| `agent_run_extractor` | AgentRun IR extraction | first contributed extractor wins |

Built-in contributions are copied into each harness before consumer contributions. A contribution
MUST NOT be installed by relying on module import side effects. Implementations MUST NOT expose an
ambient mutable registry as a contribution mechanism.

A facet or AgentRun extractor MUST NOT replace the renderer. A facet MUST NOT recurse through the
analysis tree itself. This keeps
cross-domain presentation policy in the harness while still allowing each domain to state which
nodes are primary, context, detail, summarized, grouped, or hidden. Findings remain renderer
inputs, so generic and domain detectors can affect emphasis without implementing presentation
code. The generic node-tree `agent` perspective remains a structure-preserving DisplayNode view;
it is not AgentRun IR and MUST NOT substitute for framework turn semantics.

A Measurement filter receives `(node, measurement, trace)` and selects already computed report
rows. It MUST be pure and MUST NOT change analysis results, detector inputs, facts or topology.
An omitted filter displays all prepared measurements; filtering every row hides Measurements.
Selection applies to HTML and Markdown; persisted analysis retains complete measurements.
`visible_measurements` / `visibleMeasurements` exposes the same projection for host-owned reports.

Merging contributions preserves declaration order. It does not execute them.

## 6. Host and Plugin boundary

A host MAY receive `TraceContributions` from a Plugin and combine them with generic contributions.
The host remains responsible for data access and side effects.

- A deterministic offline host such as `doctor trace` SHOULD use specs, transforms, measurers, detectors,
  facets, and any contributed AgentRun extractor, then emit IR or a report from the same scoped
  harness.
- An interactive investigation host MAY additionally request facts such as `curl` or
  `messages`.
- Evidence probes are host capabilities, not ambient Plugin contributions, and MUST be enabled at
  the call site.

## 7. Conformance

Shared fixtures live under [`conformance/trace`](../conformance/trace). Every implementation
SHOULD:

1. normalize the shared raw-span fixture;
2. run it through a scoped harness with the declared generic specs;
3. compare its canonical Analysis IR exactly with the shared expected JSON;
4. verify that transforms, measurers, detectors, and facets contributed to one harness do not
   affect another harness in the same process.

Implementations that expose AgentRun IR SHOULD also run the shared AgentRun conformance case and
compare its canonical JSON exactly.

Built-in millisecond measurements round non-negative values to three decimal places, ties upward.
Numeric JSON equality follows JSON number semantics; `8000` and `8000.0` are equivalent.

## Ordinary HTTP diagnostics

The built-in `http_slow_request` and `http_serial_same_api` detectors operate on physical
HTTP request observations without domain contributions. They emit warning findings and
retain the source span IDs for inspection; a warning identifies an optimization candidate,
not a proven root cause.

- A single ordinary request warns above 200 ms (strictly greater). Direct client/server
  pairs count once; the longest observed duration is checked, without adding descendants.
- Consecutive non-overlapping calls by the same service under the same physical parent
  warn from two calls onward when method, destination and route match. A different API,
  streaming call or concurrent call interrupts the sequence. Query and body differences
  do not split an API, except the RPC `Action` query parameter. Server route templates
  take precedence when a paired server observation exists.
- Sequence wall-clock, request total and gaps use the caller's timestamps throughout.
  Server clock offsets cannot alter caller ordering or create artificial gaps.
- Explicit SSE headers, request bodies with `stream=true`, recognized model-call ancestry
  and model endpoints (`/chat/completions`, `/embeddings`, `/rerank`) exclude a request.
  An enclosing business request remains eligible. Missing streaming metadata is not
  evidence that a request is non-streaming; the warning requires contextual review.
- Each detector retains its ten largest requests/sequences and reports truncation.
  Findings contain route/destination and timing evidence, never request bodies or credentials.

Protocol extraction accepts standard HTTP method/URL/route/address attributes and
`http.request.body[.json]`, `http.request.headers`, `http.response.headers` when present.
Vendor-specific telemetry must be mapped by the consuming integration. Both implementations
consume `conformance/trace/cases/http-detectors.json`.

### HTTP sequence display

Generic HTTP specs retain recognized requests as individual nodes so a sequence can be
expanded to inspect each call. When supplied with `http_serial_same_api` findings, the
standard display renderer projects their members into collapsed `Group` rows with a warning
label, call count, wall-clock duration, HTTP total and gap. It reuses the finding's caller
span IDs and timing values; rendering does not rerun HTTP detection or sum server timings.

Only distinct, consecutive siblings (including roots) can form a group. Missing or fused
members, different modeled parents, intervening nodes or explicit facet groups/hide/summary
operations leave the existing layout intact. Original nodes, parent edges and findings
remain available. Without findings, no HTTP sequence groups are inferred.

Adjacent HTTP rows are then collected by caller service within the same modeled parent.
The service `Group` is expanded by default, with collapsed same-API groups and individual
calls inside it. Service changes or non-HTTP rows break the collection; separate physical
callers never become one API sequence. Nested groups use `Group.children` layout operations,
while `Group.nodes` keeps the original member identities for timing and inspection.


## Detector dependencies

An implementation supporting detector composition MUST accept definitions with an explicit
`id`, direct dependency IDs (`requires`), and a plain `detect(input, context)` handler. Runtime
function names and registration order MUST NOT define the identity or dependencies of explicit definitions.
This capability does not change node detector grain or require a separate composite type.

Before reading trace evidence, the executor MUST reject duplicate/empty IDs, unknown dependencies,
unknown selected IDs, and dependency cycles. Selection MUST expand transitive dependencies. Each definition
MUST execute at most once per execution unit: the same trace/node within one analysis, or the
same Dataset within one Run. All direct dependencies MUST finish before its handler starts.
One ID has one bound configuration in a Run. Re-evaluation MUST create independent results.

`context.result(id)` MUST access only declared direct dependencies and MUST NOT schedule execution.
It MUST expose execution success/failure, error details, and iterable structured Findings. Successful
empty output MUST remain distinguishable from failure, including failure after partial output. Business
coverage remains in the findings and MUST NOT be inferred from execution success alone. Consumers MAY
produce partial conclusions from failed dependencies, but MUST identify missing or failed evidence.
An ordinary detector failure MUST NOT prevent unrelated detectors from running.

A Run MUST persist selected and resolved detector identities, dependencies, execution status, and output
provenance. A detector's identity MUST be separate from a Finding's rule source. Failed execution MUST be
visible in Run status and the report even when no Finding was produced. Output storage MAY be streamed;
composition MUST NOT require all trees or findings to remain resident. Evidence caching and Run-local
execution deduplication have independent lifetimes.

Planning cases are in `conformance/trace/detector-dependencies.json`. Python implements this capability;
TypeScript does not yet implement this detector composition contract. The shared contract is independent of
Python decorators, TS function syntax, and either implementation's storage paths.

Node and Dataset execution MUST share the definition and dependency semantics; input/context pairs
are expressed through the generic Detector type. Dependencies MUST resolve in the same grain's registry and current unit;
cross-grain references MUST be rejected. Node execution MUST preserve post-order between nodes and
use dependency order within each node. Full single-trace analysis and explicit detector selection MUST
use the same invocation semantics. Failure status MUST survive analysis snapshots and Dataset reports.
Result access MUST be independent of whether output is in memory or persisted in files.
