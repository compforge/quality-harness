# Case Runner

Execute prepared canonical Cases through a caller-owned transport and retain the original observation
alongside protocol judgment. The TypeScript package currently supports the spec-case HTTP profile.

A `PreparedHttpCase` contains a canonical `case` and a runtime `target`. The Case carries stable input
and optional `judge.e2e.http`; target URLs and authentication stay outside Case identity. The caller
selects an entrypoint, execution location and timeout/byte budgets, and injects a capture function.
`executeHttpCase` constructs one request and evaluates the returned response; it never selects a
Kubernetes Pod, opens a Host connection, follows redirects, retries or writes reports.

Missing HTTP criteria yields `observed`; incomplete response capture yields `failed`, including after
HTTP 200. Scheduling alternate entrypoints and interpreting their combined result belongs to the caller.
A transport must honor the supplied signal and budgets and retain interrupted response evidence.

```ts
import { executeHttpCase } from "@compforge/case-runner";
const result = await executeHttpCase(prepared, "primary", budget, signal, captureFromSelectedTarget);
// result.observation is the original captured evidence; result.judgment is independent assessment.
```

Run `make lint`, `make test`, and `make build` from this directory. Runtime target data may contain secrets;
callers must redact credentials and signed query values before storing or rendering evidence.
