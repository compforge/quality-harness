# Harness Common for Python

Standard-library contracts shared by the quality SDKs. Import public models and lifecycle
contracts from `harness_common`; concrete protocol clients live in `harness_toolbox`.

## Upgrade to 0.6

Package-root exports such as `from harness_common import Service, ClientManager, Execution`
retain their identity and behavior. Internal modules are now grouped by responsibility:

| Previous module under `harness_common` | New module |
| --- | --- |
| `repository`, `component`, `product`, `forge`, `service`, `environment`, `host`, `workload`, `resource`, `deployment` | `system.<module>` |
| `client`, `datasource`, `context`, `fixture` | `access.<module>` |
| `artifact`, `experiment`, `operation`, `outcome`, `overlay`, `reducer`, `run`, `verdict` | `execution.<module>` |
| `execution` | `execution.execution` (`from harness_common.execution import Execution` also remains valid) |
| `llm` | `harness_toolbox.llm` |

`identity` and `report_kit` keep their existing paths. Replace old deep imports; no flat
compatibility modules are shipped. For example:

```python
from harness_common.execution.verdict import RunVerdict
from harness_common.execution.run import run_dir_for
```

Install `harness-toolbox[llm]>=0.6,<0.7` for `LLMClient`, `LLMConfig` and `ChatResult`.
The old `harness-common[llm]` extra is removed. The LLM re-exports from `eval_harness`
and the `eval_harness.llm` compatibility package are removed; import directly from
`harness_toolbox.llm`. Client request, timeout and retry behavior is unchanged.

Upgrade `harness-common` and `harness-toolbox` together to 0.6; `quality-harness>=0.5.0`
selects this compatible pair and the LLM extra. Publish common before toolbox, then
quality-harness, so every dependency is available when a downstream package is installed.

## PullRequest

`PullRequest` identifies a PR/MR by its target `Repository` and repository-local number.
State, branches, source repository, revisions and provider timestamps are optional facts.
See [the shared contract](../../../docs/pull-request.md) for identity and unknown-state semantics.
Forge access and polling remain consumer/toolkit responsibilities.
