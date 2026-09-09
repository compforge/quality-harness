# quality-harness

**Test software and agents. Turn execution evidence into quality assessments.**

[中文](README.zh-CN.md)

quality-harness provides SDKs for API testing, agent evaluation, performance testing, trace analysis, and agent trajectory analysis. Use them to run your project's tests or assess evidence from existing runs, then produce reports and machine-readable verdicts for developers, CI, and agent workflows.

## What you can check

| Question | Capability | SDKs |
|---|---|---|
| Do service APIs behave as expected? | **e2e** — run cases and check API contracts | [Python](docs/e2e-harness.md) / [Go](examples/README.md#go-service) |
| How good are an agent's outputs? | **eval** — evaluate results and compare experiments | [Python](sdks/python/eval_harness/README.md) |
| How does the system perform under load? | **perf** — measure latency, throughput, and resource use against declared targets | [Python](sdks/python/perf_harness/README.md) / [TypeScript](sdks/typescript/perf-harness/README.md) |
| Where does a call chain show abnormal behavior? | **trace** — analyze spans, locate anomalies, and investigate causes | [Python](docs/trace-harness.md) / [TypeScript](sdks/typescript/trace-harness/README.md) |
| Are an agent's decisions and actions effective and efficient? | **trajectory** — measure cost, detect patterns, and verify behavior | [Python](sdks/python/trajectory_harness/README.md) |

## Get started

Choose an example for your scenario:

| Example | Use it for |
|---|---|
| [API cases](examples/api-test/README.md) | Data-driven requests and assertions |
| [Python service tests](examples/README.md#python-service) | Tests with setup, multiple operations, and cleanup |
| [Go service tests](examples/README.md#go-service) | Case execution with `go test` and aggregated verdicts |
| [Agent evaluation](examples/agent-test/README.md) | Dataset-driven quality assessment |

To run the Python API example, start from a source checkout with Python 3.11+ and `uv` installed. Configure the example's cases and service URL for your target, then run:

```bash
cd sdks/python
uv sync
export WIDGET_BASE_URL=http://localhost:8080
export WIDGET_TOKEN=...
uv run e2e run ../../examples/api-test/cases.yaml \
  --config ../../examples/api-test/config.yaml \
  --runs-dir ../../runs
```

The target service must be running and expose the endpoints used by the cases. Results are written to a run directory under `runs/`, including `verdict.json`. Skipped cases and execution errors remain visible in the result.
