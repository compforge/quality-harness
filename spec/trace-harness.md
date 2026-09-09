# Trace Harness 1.0

## 1. Scope

Trace Harness defines a language-neutral pipeline for turning raw distributed-trace spans into
logical analysis nodes, findings, and presentation trees. Python and TypeScript are peer
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
raw spans -> normalize -> assemble -> eager features -> diagnose -> render
                                      \-> lazy features on explicit demand
                                      \-> probes on explicit host demand
                                      \-> NodeTreeExtractor -> AgentRun IR -> render
```

1. `normalize` maps backend-specific span documents to `NormSpan` without assigning a semantic
   kind.
2. `assemble` applies ordered `KindSpec` values, fuses physical spans into logical `Node` values,
   creates parent edges, and bakes eager features and projections.
3. `diagnose` emits `Finding` values. Physical errors and per-kind rules run before scoped
   detectors. Detectors run in post-order and receive findings accumulated so far.
4. `render` applies scoped, declarative facets to `Node + Finding` and produces a display tree.
   Perspective and layout are orthogonal: `full` or `agent` decides which parts of the same node
   tree receive emphasis, while `tree` or `flame` decides how that projection is drawn. Facets
   declare presentation intent such as perspective level, row projection, and child layout; the
   harness owns traversal, display-tree construction, and output serialization. A renderer MAY
   expose lazy features in node details.
5. An `agent_run_extractor` MAY read the complete node tree and raw trace context, apply one
   Agent Framework's correlation rules, and emit AgentRun IR. The harness validates and renders
   the resulting IR; it MUST NOT guess framework-specific turn, tool, or operation semantics.
6. A probe MAY write evidence, but MUST run only after the host explicitly enables it. Importing
   a package, constructing a harness, assembling, diagnosing without probes, and rendering MUST
   NOT create evidence files.

Only `assemble` may create or change logical node parent edges. Later stages MUST be
structure-preserving. A coarse perspective MAY replace an uninteresting display path with a
synthetic context connector, but it MUST retain the represented node IDs and MUST NOT rewrite
`Node.parent_node_id`.

## 3. Analysis IR

The canonical JSON projection is `trace-harness/analysis@1`, defined by
[`analysis.schema.json`](../schema/trace/v1/analysis.schema.json).

- `NormSpan` is one normalized physical span and retains raw data for provenance.
- `Node` is the node-grain Unit Observation: one logical operation backed by one or more physical spans.
- `facts` contains named, JSON-compatible values derived from raw domain fields.
- `Finding` is the diagnostic output of detection attached to a node, trace, or cohort. A finding is not a verdict.
- `brief` is the baked, language-neutral field projection used by renderers.

Implementations MUST order nodes by `(start_ms, node_id)` and findings by
`(scope, ref, source, severity, note)` when producing the canonical JSON projection. Runtime
collections do not otherwise need to use this order.

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
five pure extension slots:

| Slot | Consumed by | Ordering rule |
| --- | --- | --- |
| `specs` | assemble and per-kind diagnose | first matching spec wins |
| `features` | eager bake and lazy detail | first applicable producer of a name wins |
| `detectors` | diagnose | declaration order within each post-order node |
| `facets` | render | highest priority wins; declaration order breaks ties; an undefined perspective level falls through |
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

Merging contributions preserves declaration order. It does not execute them.

## 6. Host and Plugin boundary

A host MAY receive `TraceContributions` from a Plugin and combine them with generic contributions.
The host remains responsible for data access and side effects.

- A deterministic offline host such as `doctor trace` SHOULD use specs, features, detectors,
  facets, and any contributed AgentRun extractor, then emit IR or a report from the same scoped
  harness.
- An interactive investigation host MAY additionally expose lazy features such as `curl` or
  `messages`.
- Evidence probes are host capabilities, not ambient Plugin contributions, and MUST be enabled at
  the call site.

## 7. Conformance

Shared fixtures live under [`conformance/trace`](../conformance/trace). Every implementation
SHOULD:

1. normalize the shared raw-span fixture;
2. run it through a scoped harness with the declared generic specs;
3. compare its canonical Analysis IR exactly with the shared expected JSON;
4. verify that features, detectors, facets, and lazy features contributed to one harness do not
   affect another harness in the same process.

Implementations that expose AgentRun IR SHOULD also run the shared AgentRun conformance case and
compare its canonical JSON exactly.

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
