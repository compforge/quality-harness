# @compforge/perf-harness

Bounded request-rate and concurrency scheduling, with separate raw facts and judgments.
Service, Execution and OperationRun reuse `@compforge/harness-common`; Cases reuse spec-case.

```ts
import { Engine, writeRunData, type Runner } from "@compforge/perf-harness";

const runner: Runner = {
  name: "chat",
  async fire({ signal }) {
    const start = performance.now();
    const response = await fetch("http://service/chat", {method: "POST", signal});
    await response.arrayBuffer(); // occupy the slot through the complete response
    return {status: response.status, duration_ms: performance.now() - start};
  },
};
const run = await new Engine({
  name: "chat-capacity",
  service: {
    name: "chat", component: {name: "api", repository: {forge: {name: "github"}, path: "org/chat"}},
    environment: {name: "dfx"}, workloads: [],
  },
  runner,
  loads: [{request_rate: 4, max_inflight: 32, hold_s: 60, cooldown_timeout_s: 180}],
}).run();
writeRunData(run, "./runs/chat-capacity/local");
```

Warmup doubles the rate from 1 every 5 seconds until the target rate or inflight cap is reached.
Hold lasts `hold_s` seconds (default 60) and replenishes vacancies at the configured target rate.
A full inflight cap pauses the source without queued or dropped arrivals. `Infinity` starts directly
in hold and replenishes available slots. Cooldown waits for calls to finish, then cancels and joins
remaining calls at `cooldown_timeout_s` (default 180).
A decreasing cap does not cancel existing calls. Each Runner must cooperate with AbortSignal and fully
consume its response; use a streaming parser with bounded buffers for real SSE. A separate `judge`
function evaluates raw Outcomes; the default checks transport/status, not business completion.

`caseSet` and `caseMix` select canonical Case IDs and experiment-local weights. `run.executions` holds
ArmRuns; each actual invocation owns one OperationRun/Outcome. Dispatch-cohort latency includes drained
responses, while completion throughput uses actual completion timestamps.

Schema 6 artifacts: `run.json`, `requests.jsonl`, `evaluations.json`, `timeseries.csv`, `verdict.json`.
`loadRun` reads them offline. The TypeScript SDK does not provide resource probes, rendered reports or
SLO evaluation. Its successful execution verdict is `skipped`; phase errors, early stops and interrupted
calls fail. Python provides the richer reporting and SLO layer using the same persisted facts.

Development from `sdks/typescript`: `bun install --frozen-lockfile`, then build `common` before
`perf-harness`. Run `bun test`, `bun run typecheck`, and `bun run build` in each package.
See [shared contract](../../../spec/perf-contract.md).
