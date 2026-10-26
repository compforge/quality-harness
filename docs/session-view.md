# Session observations

Trace Harness and Trajectory Harness export Agent Ledger's `observation.SessionView` for following
and analyzing one Agent Session. SessionView is currently the observation package's main capability.
The wire schema is `agent-ledger/session-view@1`; each JSON file contains exactly one Session.
Trace Node/analysis and ATIF remain the domain evidence and evaluation contracts.

## Dependencies and ownership

TypeScript uses the public `@compforge/agent-observation@0.1.0` model/renderer and
`@compforge/agent-common@0.1.1`. Python imports `agent_ledger.observation` from the immutable merged
Agent Ledger Git revision pinned in `pyproject.toml` and `uv.lock`. The PyPI distribution named
`agent-ledger` belongs to another project. No Ledger Store or Action/Attempt ingestion is needed.

Source adapters own attribution and mapping. Observation owns the common read model and rendering.
A Session can span several runs and inputs. A business Unit or a Run alone does not establish an
Agent Session. Cross-Session business sequencing, such as a Unit passing between review agents,
belongs to a future Timeline; the export index is only a file catalog.

## Trace

Python `trace_harness.session_views(ir, context)` and TypeScript `sessionViews(ir, context)` return
one SessionView per explicitly attributed Session. The extractor may set `AgentRun.session` to the
shared `SessionRef(source, id)`. Runs with the same reference are grouped as `trace.run` Operations
inside that Session; Run outcome remains on that Operation and never closes the Session.

Nested agents with another Session reference are exported separately. Tool observations carry
references to related Sessions rather than embedding their execution bodies. A child does not
inherit a Session identity from its parent. Unattributed Runs stay available in the source Trace IR;
Python's export index lists them as `unassigned_runs`, without fabricating identities.

Source node references, calls and timing evidence are retained. Unknown statuses remain unknown,
zero ingestion timestamps remain unavailable, and a request is never reconstructed from transcript
messages. Trace IR's ambiguous `model` value remains `attributes.trace_model` rather than being
claimed as the actual response model.

`dump_analysis` writes `<stem>.session-views/index.json` beside the saved analysis, plus individual
SessionView JSON files. The index maps Session references to stable hashed filenames, so native IDs
cannot escape the output directory. Repeated exports overwrite the same Session files. Only files
listed in the current index are selected; older files are not part of the current export. The saved
analysis preserves typed Session attribution for offline reload.

## Trajectory

`trajectory_harness.session_views(trajectories)` groups explicitly declared ATIF `session_id` values
under the `atif` source namespace. Trajectory/Step IDs retain evidence identity inside each Session;
multiple trajectory documents with the same declared Session can contribute without mixing other
Sessions. Missing Session identity produces no fabricated SessionView. Report indexes record those
documents as `unassigned_trajectories`.

User messages become Input observations without guessing their delivery mode. Single-call model
steps use recorded requests when available; missing requests are issues. Aggregate model-call
counts remain Operations. ATIF agent, execution and extension metadata remain source evidence,
without being promoted into a Session completion claim.

Tool-result association prefers exact nonempty IDs with a unique call match. One call and one
result in the same source Step can be associated when at least one ID is absent and no supplied IDs
conflict; the view marks this as `single-call-step` and does not invent a native ID. Equal counts of
multiple calls/results do not prove their correspondence. Unmatched results remain response-only
ToolCall observations with association issues; unmatched proposals remain Operations. No result is
discarded for lack of a request ID.

`TrajectoryReportBuilder.write` and `rerender` write `session-views/index.json` and one JSON per
Session alongside `report.html`, using only trajectories selected in the saved analysis. Re-rendering
uses saved artifacts without collection, model calls or repeated evaluation. CCR can consume this
same report export after updating its Quality Harness dependency and artifact paths.

## Render one Session

```sh
npx --package @compforge/agent-observation session-view session-views/<file-from-index>.json -o session.html
```

Or use the renderer directly with the TypeScript Trace export:

```ts
import { sessionViews } from "@compforge/trace-harness";
import { renderHTML } from "@compforge/agent-observation";

for (const view of sessionViews(ir, context)) {
  const html = renderHTML(view, `Session ${view.session.id}`);
}
```

The renderer accepts one SessionView, uses the same JSON contract across languages, and does not
fetch source evidence or resume any agent execution.
