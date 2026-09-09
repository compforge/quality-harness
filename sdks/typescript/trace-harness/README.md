# @compforge/trace-harness

TypeScript implementation of this repository's Python `trace_harness` core. It consumes Jaeger
span documents, fuses physical spans into logical nodes, derives facts and findings, and renders
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
const html = harness.renderInteractive(context, harness.diagnose(context));
```

Domain-specific behavior stays in the consumer and is passed explicitly as scoped
`TraceContributions` (`specs`, `features`, `detectors`, declarative `facets`, and an optional
`agentRunExtractor`). The extractor implements `NodeTreeExtractor<AgentRunIR>` and owns the
framework-specific run/turn/call correlation. The harness validates the AgentRun IR and owns both
NodeTree and recursive AgentRun rendering, including nested operations and runs owned by a ToolCall
or Operation.
The language-neutral contract is
[`spec/trace-harness.md`](../../../spec/trace-harness.md).
