# @compforge/trace-harness

Trace Harness normalizes source evidence (usually trace/span data) into logical Nodes, computes
Measurements, applies Detectors, and presents the prepared results through Views.

```text
Source → normalized spans → Nodes → Measurer → Measurement
                                  → Detector → Finding
                                  → buildView → render
```

Measurements describe what happened under a declared calculation convention. Detectors apply
chosen rules and expectations, using facts and Measurements as inputs. A View selects and organizes
existing data; rendering changes its format. Neither stage fetches evidence or runs analysis.

```ts
import { TraceHarness, genAiSpecs, normalizeJaegerSpans, render } from "@compforge/trace-harness";

const harness = new TraceHarness({ specs: genAiSpecs() });
const trace = harness.assemble(normalizeJaegerSpans(rawJaegerDocuments));
const analysis = await harness.analyze(trace);
const view = harness.buildView(analysis, { scope: "trace", view: "default" });
const html = render(view, { format: "html" });
const text = render(view, { format: "text" });
const json = render(view, { format: "json" });
```

The trace HTML is a self-contained offline file with compressed evidence, expandable nodes, span
inspection, search and a flame graph. Browser memory remains a practical size limit.

## Select a subject and focus

| Subject | View | Selected content |
| --- | --- | --- |
| trace | default | Node tree organized by contributed facets |
| trace | agent | Prepared SessionView, turns, model/tool calls and operations |
| node | default | Basic node facts, details, Findings and span evidence |
| node | measurements | Node Measurements and prepared operation aggregates |
| node | timeline | Prepared calls through the node's end, with source references |

The default view name is `default`. `offset` and `limit` page selected rows, groups or calls; outputs
include total count and `next_offset`. Parent IDs and depth survive page boundaries. JSON is the
View's direct serialization; `render(JSON.parse(json), {format: "html"})` needs no analysis context.

```ts
// Prepare only the needed derived data. This is explicit analysis work, before building views.
harness.prepare(analysis, { sessionViews: true, callBreakdowns: [nodeId] });
const agent = harness.buildView(analysis, { scope: "trace", view: "agent", limit: 100 });
const metrics = harness.buildView(analysis, { scope: "node", nodeId, view: "measurements" });
const timeline = harness.buildView(analysis, { scope: "node", nodeId, view: "timeline", limit: 100 });
console.log(render(agent, { format: "text" }));
const combinedHtml = render([view, agent], { format: "html" }); // Same trace, adjacent view buttons.
```

SessionView is a derived representation retaining original Node/span references. Its framework-specific
extraction belongs to a contributed `sessionViewExtractor`. Missing extraction is unavailable, never
inferred from node names. The same selected Agent rows feed HTML, text and JSON.

## Measurements and ownership

Each spec has a `kind` and `scope` (`trace` or `node`). Results attach to the containing trace
(`analysis.measurements.trace`) or a node (`analysis.measurements.nodes[nodeId]`), independently of
raw observations. `get(null, kind)` reads a trace measurement; `get(nodeId, kind)` reads a node one.
The measurement kind is distinct from a call's kind. Error, not-applicable and measured zero differ.

`calls_until_node_end` belongs to its node but includes the whole observed trace prefix, not just its
subtree. Counts include calls starting at the cutoff; in-flight duration is clipped to it.
`call_breakdown` stores explicitly prepared clipped calls and operation groups, enabling the
measurement bar chart and timeline. Overlapping/nested duration sums are not exclusive waiting time
or critical-path contributions. Changing a detector's threshold never changes these measurements.

A contributed `measurementFilter` selects which nodes show the yellow **耗时分析** button in the trace
report. Prepare call breakdowns for those nodes to enable the dialog's aggregate and timeline tabs.
Explicit node views read all saved measurements independently of this display filter.

`analysisSnapshot` / `loadAnalysis` use `trace-harness/analysis@4`, preserving measurements and optional
SessionView derivations. Offline rendering never reruns extensions. Acquire full source evidence before
rendering if span inspection is needed; a saved analysis without raw spans says they are unavailable.

## Domain contributions and managed loading

Pass `TraceContributions` explicitly: specs, transforms, measurers, detectors, facets and an optional
SessionView extractor. Registries belong to the harness instance; importing a domain has no side effects.
`TraceContext.tree()` exposes the structural `NodeTree`; it is independent of presentation Views.

For managed Source selection, caching, loading budgets and lease lifecycle, see [loading](docs/loading.md).
The shared [contract](../../../spec/trace-harness.md) and [fixtures](../../../conformance/trace/) define
cross-language behavior. Python exposes idiomatic `build_view` and `render(view, format=...)` APIs.

## Development

```sh
bun install --frozen-lockfile
make lint
make test
```

This release intentionally removes the old inspection/render entrypoints and `view()` relationship
index naming. Consumers must migrate to `prepare`, `buildView`, `render`, `tree()` and measurement kinds.
