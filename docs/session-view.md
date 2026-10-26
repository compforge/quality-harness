# Session observations

Trace Harness and Trajectory Harness export Agent Ledger's `observation.SessionView` for following
and analyzing one Agent Session. SessionView follows a Session; JobView follows business work across Sessions.
The wire schema is `agent-ledger/session@1`; each JSON file contains exactly one Session.
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

## Agent loop and collaboration boundaries

SessionView observes both task-oriented loops (work toward a result and finish) and continuous loops
(receive followup/steer inputs over time). A process exit, task completion or run boundary does not
by itself establish a new Session identity.

When a task-oriented agent invokes another agent through a ToolCall, the call can reference the
callee's Session. Consumers with both snapshots can follow that explicit invocation relationship.
Each Session keeps its own nodes; a child Session's execution is not copied into its caller's model.

A business Unit passing through two continuously running agents is a different observation question.
For example, R1 and R2 may each process many Units in one Session; one Unit's work spans only a portion
of each Session. Tracking that Unit across Sessions requires business correlation and selection of
relevant execution segments. This is deferred to a future Timeline, not encoded as Session nesting.

## Trace

Domain extractors directly produce `observation.SessionView` values. Python contributes
`session_view_extractor: NodeTreeExtractor[tuple[SessionView, ...]]`; TypeScript contributes
`sessionViewExtractor: NodeTreeExtractor<SessionView[]>`. Use `harness.extract_session_views(context)`
or `harness.extractSessionViews(context)`, or explicitly prepare `analysis.session_views` /
`analysis.sessionViews`. Trace does not define another AgentRun/Turn/call model or conversion layer.
Import agent types directly from `agent_ledger.observation` or `@compforge/agent-observation` and
shared call/identity types from `common`.

The extractor owns Session attribution, framework turn boundaries and invocation association.
It groups all observed nodes for the same `(source, id)` into one SessionView. A run can be an
Operation inside that Session if the source supplies that boundary. Run outcome stays on the
Operation, never on the Session root. Missing Session identity leaves evidence in Trace Nodes;
do not manufacture a Session from a trace or business Unit ID.

Trace validates the shared model, rejects duplicate Session references in one extraction, and checks
local evidence references. `SourceRef(source="trace-node", id=node_id, uri="trace:<trace_id>")`
links an item to a Trace Node and its spans. An omitted URI means the current trace. References to
other trace URIs remain evidence but are not bound to local nodes with coincident IDs. Shared
Observation validation owns item IDs, call links and timing; asynchronous children need not fit a
parent time window. No second Trace-specific Agent schema is maintained.

ToolCall `sessions` references remain independent Session identities. The Trace viewer can expand
supplied Session snapshots at the call site without changing stored models. Repeated call sites get
distinct display IDs; cycles and unavailable targets remain explicit references. It never fetches a
missing Session or assumes that a reference denotes only a particular slice of a continuous Session.
Operation groups work through `nodes`. Its inherited `session` identifies execution ownership;
references to called Sessions remain on ToolCall `sessions`.

Source requests, response models, timing and unknown values are supplied directly using Observation's
contract. Do not reconstruct unobserved prompts or claim a requested model as a response model.
Explicit null content remains explicit. The renderer keeps evidence drill-down and error visibility.

`analysis@4` snapshots store `session_views` directly. `dump_analysis` also writes
`<stem>.session-views/index.json` and one SessionView JSON file per Session, using stable hashed
filenames. The current index selects exports; stale files are not current exports. Offline loading
validates saved observations and re-rendering does not re-extract or acquire evidence. Unprepared
observations are absent; a prepared empty collection remains empty after saving and reloading.

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
// harness is a TraceHarness configured with a sessionViewExtractor.
import { renderHTML } from "@compforge/agent-observation";

for (const view of (harness.extractSessionViews(context) ?? [])) {
  const html = renderHTML(view, `Session ${view.session.id}`);
}
```

The renderer accepts one SessionView, uses the same JSON contract across languages, and does not
fetch source evidence or resume any agent execution.
