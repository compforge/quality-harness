# @compforge/trace-harness

TypeScript implementation of the Trace Harness contract. It consumes Jaeger
span documents, fuses physical spans into logical nodes, derives facts, computes cumulative call measurements and diagnoses findings, and renders
a self-contained interactive HTML report. A domain may additionally extract AgentRun IR from the
complete node tree; the same report then exposes an Agent view with ordered turns, model calls,
tool calls, and framework operations.

```ts
import {
  genAiSpecs,
  normalizeJaegerSpans,
  TraceHarness,
} from "@compforge/trace-harness";

const spans = normalizeJaegerSpans(rawJaegerDocuments);
const harness = new TraceHarness({ specs: genAiSpecs() });
const context = harness.assemble(spans);
const analysis = await harness.analyze(context);
const html = harness.renderInteractive(context, analysis.findings, {
  measurements: analysis.measurements,
});
```

The report is one offline HTML file: open it directly in a browser, without a server or extraction.
Its embedded ZIP stores a lightweight tree index separately from node and span details. Selecting a
node inflates only that node's details and the selected span; folded branches mount when expanded.
Large text has a bounded preview with a complete-content download. Compressed data still resides in
browser memory, so this format does not remove the browser's overall memory limit.

Domain-specific behavior stays in the consumer and is passed explicitly as scoped
`TraceContributions` (`specs`, `transforms`, `measurers`, `detectors`, declarative `facets`, and an optional
`agentRunExtractor`). The extractor implements `NodeTreeExtractor<AgentRunIR>` and owns the
framework-specific run/turn/call correlation. The harness validates the AgentRun IR and owns both
NodeTree and recursive AgentRun rendering, including nested operations and runs owned by a ToolCall
or Operation.
The language-neutral contract is
[`spec/trace-harness.md`](../../../spec/trace-harness.md).

Transform existing facts into additional facts by contributing `FactTransform` values. Each
transform declares `produces`, `applies` and `compute`; a curl representation and a derived HTTP
status use the same contract. Request output names with `harness.transform(node, context, "curl")`
or `harness.transformAll(context, "curl")` before rendering. Outputs become `node.facts` and are
cached within that trace. The renderer never runs transformations.


For large or remotely accessed evidence, use a managed Session. It keeps a fixed Dataset, loads only
needed fields by default, and shares evidence reads across analyses. Choose preloading with
`config: { lazy: false }`; this prepares the active trace, not the whole Dataset.

```ts
import { JaegerFileSource, TraceHarness, genAiSpecs } from "@compforge/trace-harness";

const harness = new TraceHarness({ specs: genAiSpecs() });
await using session = harness.open(new JaegerFileSource("traces.jsonl"), {
  workDir: "./trace-workspace",
  config: { lazy: true, concurrency: 8, activeTraces: 4 },
});
const dataset = await session.select({ limit: 100 });
for await (const input of session.trees(dataset)) {
  const result = await session.analyze(input);
  await session.prepareView(result, { full: true });
  const html = harness.renderInteractive(result.trace, result.findings, {
    measurements: result.measurements,
  });
  // Write each HTML string to its destination before requesting the next trace.
}
```

A Session owns its Source; close it with `await using` or `try/finally`. Remote consumers implement
`Source` using their existing clients. See the [loading guide](docs/loading.md) for dependency declarations,
individual trace leases, persisted evidence reuse, and import/resource limits. `analyze` and `diagnose`
are asynchronous; pure `assemble`, `measure`, fact transformation and rendering remain synchronous.
