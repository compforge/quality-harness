# quality-harness

**Test software and agents. Turn execution evidence into quality assessments.**

[中文](README.zh-CN.md)

quality-harness provides SDKs for API testing, agent evaluation, performance testing, trace analysis, and agent trajectory analysis. Use them to run your project's tests or assess evidence from existing runs, then produce reports and machine-readable verdicts for developers, CI, and agent workflows.

The repository currently provides domain SDKs, shared result contracts, and platform tools. A project-level Harness that discovers a project's quality capabilities, selects checks, and explains results and coverage gaps is a long-term plan. See the [Quality Harness design](docs/quality-harness.md).

## What you can check

| Question | Capability | SDKs |
|---|---|---|
| Do service APIs behave as expected? | **e2e** — run cases and check API contracts | [Python](docs/e2e-harness.md) / [Go](sdks/go/) |
| How good are an agent's outputs? | **eval** — evaluate results and compare experiments | [Python](sdks/python/eval_harness/README.md) |
| How does the system perform under load? | **perf** — measure latency, throughput, and resource use against declared targets | [Python](sdks/python/perf_harness/README.md) / [TypeScript](sdks/typescript/perf-harness/README.md) |
| Where does a call chain show abnormal behavior? | **trace** — analyze spans, locate anomalies, and investigate causes | [Python](docs/trace-harness.md) / [TypeScript](sdks/typescript/trace-harness/README.md) |
| Are an agent's decisions and actions effective and efficient? | **trajectory** — measure cost, detect patterns, and verify behavior | [Python](sdks/python/trajectory_harness/README.md) |

Each SDK covers a distinct question. Current e2e support focuses on service APIs; trace and trajectory can analyze recorded evidence directly.

## How you use it

Bring your test cases and acceptance criteria, or recordings you want to analyze. Choose the SDK for the question you need to answer:

```text
Run cases or experiments → collect execution evidence
Import existing recordings → reuse execution evidence
                                      ↓
                     Analyze, measure, and verify
                                      ↓
                         Reports and verdicts
```

A **Case** describes reusable test inputs and expectations. A **Dataset** holds evidence and annotations for repeated assessment. A **Verdict** records the resulting quality conclusion in a machine-readable form. Keeping evidence separate from assessments lets you apply different checks to the same dataset without rerunning the target.

Your project owns its cases, test actions, and acceptance criteria. Your deployment workflow supplies the target environment and credentials. quality-harness provides execution, analysis, and reporting mechanisms; [spec-case](https://github.com/compforge/spec-case) defines the shared Case format.

For tests that need environment control, the [platform toolbox](docs/toolbox.md) provides Kubernetes workload operations and observation in Python and Go. Your project decides which resources to target and what constitutes recovery or acceptable performance.

## Get started

Choose an example for your scenario:

| Example | Use it for |
|---|---|
| [API cases](examples/api-test/README.md) | Data-driven requests and assertions |
| [Python service tests](examples/python-service/) | Tests with setup, multiple operations, and cleanup |
| [Go service tests](examples/go-service/) | Case execution with `go test` and aggregated verdicts |
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

For the Go example, configure a deployed service as described in the [example](examples/go-service/), then run from the repository root:

```bash
cd examples/go-service
export ASANDBOX_BASE_URL=http://localhost:8090
export EXAMPLE_TOKEN=...
go test -tags=e2e -v ./...
```

## Further reading

- [Shared concepts and contracts](docs/kernel.md)
- [Project-level Quality Harness design](docs/quality-harness.md)
- [Platform toolbox](docs/toolbox.md)
- [Development and testing](AGENTS.md#开发与测试)

quality-harness is an early public project. SDK feature coverage varies by language; use the capability guides above to choose an implementation.
