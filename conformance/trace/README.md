# Trace Harness conformance fixtures

Python and TypeScript MUST consume the same files in this directory.

- `fixtures/genai-basic.jsonl` is the raw Jaeger-span input.
- `cases/genai-basic.analysis.json` is the exact canonical Analysis IR after generic GenAI
  assembly and diagnosis.
- `cases/genai-basic.agent-run.json` is the exact AgentRun IR emitted by the sanitized fixture
  extractor in both implementations; it covers run-level and turn-level operations.

Implementation-specific tests additionally verify that one `TraceHarness` instance cannot see
another instance's contributed transforms, measurements, detectors, or facets.

`cases/measurements.json` defines hand-calculated prefix cases: serial, parallel/in-flight,
cutoff equality, zero duration, other branches/roots, client/server clock skew, model and stream
HTTP, and empty calls. Both SDKs also compare the interval sweep against an independent naive
oracle. `genai-basic.analysis.json` is the canonical analysis@2 artifact.
