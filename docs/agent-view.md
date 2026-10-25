# Shared agent execution views

Trace Harness, Trajectory Harness and their consumers export `agent-ledger/view@1` for inspection
with the Agent Ledger View model and renderer. Trace nodes and ATIF remain the source evidence;
this projection does not replace measurements or evaluation results.

## Trace

Python `trace_harness.agent_view(ir, context)` and TypeScript `agentView(ir, context)` consume
already prepared AgentRun extraction. Trace node references, call bodies, nested runs and source
attributes are retained. Unknown statuses stay unknown. Zero timestamps remain unavailable.
Python `dump_analysis` writes an adjacent `<stem>.agent-view.json` when agent runs were prepared.
TypeScript `renderAgentView(ir, context)` produces standalone HTML through the shared renderer.

## Trajectory and CCR

`trajectory_harness.agent_view(trajectories)` projects ATIF into shared Runs, Inputs and observed
calls. Exact recorded model requests are used when available. Missing requests are explicit issues;
transcripts are never replayed into a synthetic prompt. Aggregated model calls stay Operations.
A tool proposal without observed results stays a proposal. ATIF source data remains attached as
attributes, including provenance and domain extensions.

`TrajectoryReportBuilder.write` and `rerender` now emit `agent-view.json` alongside `report.html`,
using only the trajectories selected in the saved analysis run. CCR's report builder inherits this
export, so historical artifacts can be rendered without another review or model call.

```sh
npx --package @compforge/agent-view agent-view agent-view.json -o agent-view.html
```

The npm renderer is the same for JSON produced by Python and TypeScript. Python currently pins
Agent Ledger to an immutable Git commit because the PyPI name `agent-ledger` belongs to another
project. No Ledger Store or Action/Attempt ingestion is needed.

Trace IR does not distinguish a requested model from the actual response model. Its `model`
therefore stays in `attributes.trace_model` rather than being assigned to a response.
