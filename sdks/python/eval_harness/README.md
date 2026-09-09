# eval_harness

Experiment-first, **table-centric** evaluation harness for agent APIs
(chat / fastresearch / deepresearch). Self-contained (no `e2e_harness` import).


## Spine

```
evalset ──( solver 补 answer / scorer 补 metric )──▶ Worksheet (大表) ──pivot──▶ report
```

One evalset, run through one or more **Arms** (comparison configurations), produces one
in-memory **Worksheet** that a **reconciler** fills cell-by-cell, and a report
renders by pure pivot.

## Concepts

| concept | what |
|---|---|
| **Experiment** | an Eval specialization of common Experiment: one named eval "question" with `service` (base SUT config) + `arms` + `evalset` + `metrics` + `weights`. |
| **Arm** | a named comparison configuration = `service ⊕ overrides`. Two layers: **heavy** (provisioned resource+sources, `prepare()`/`clean()`/`key`, shared across same-key Arms) + **light** (model/params, per call). `Arm.key` hashes only heavy-affecting fields → light-only differences share one provisioned resource. |
| **Trial** | one real execution of an Arm over the evalset; its cells are recorded in the Worksheet under the explicit `arm_id`. |
| **EvalSet / Case** | Runtime projection of one canonical spec-case CaseSet. Eval consumes its identity, sources, facet vocabulary and cases unchanged, then interprets `input.query` and `judge.eval` through `eval_view`. |
| **FacetSchema** | per-facet value domain (constrained / `open` / `ordered`); validated at load (so a free-string field can't rot). Replaces flat tags. |
| **Worksheet** | the Eval specialization of common ExperimentRun: the big table whose rows = (arm_id × case), with PENDING/OK/FAILED cell state. It is the engine's single truth and checkpoint Artifact. |
| **MetricResult** | dual channel: **quality** (0~1 → weighted overall) vs **measurement** (value+unit, mean/p50/p95, excluded from overall). |
| **reconciler** | fills missing cells (缺啥补啥) under per-endpoint rate gates + cell deps; pipelined; resume = reload + fill gaps. |
| **LLMSpec** | optional typed LLM config on `service.llm` (`model`/`base_url`/`temperature`/`max_tokens`/`api_key`/`extra`) — the common comparison dimension. Light (not in `Arm.key`), so swapping `llm.model` shares the prepared resource; matrix-sweepable (`matrix: {llm.model: [...]}`); the rate-gate key derives from `(base_url, model)`. eval_harness only carries+resolves it; the consumer's Solver maps it to the SUT and the SUT decides whether to honor it. |

**`${ENV}` interpolation**: string values in `experiment.yaml` support `${VAR}` /
`${VAR:-default}` (resolved at load from the environment; unset + no default fails
loud) — keeps secrets like `service.llm.api_key` out of yaml/git:

```yaml
evalset: cases/chat.yaml  # canonical CaseSet; also reusable by Perf
service:
  name: chat
  component:
    repository: {forge: github, path: example/chat}
    name: api
  environment: {name: dev, kubeconfig: "${KUBECONFIG:-}"}
  llm: {base_url: "${AIGW_BASE}", api_key: "${AIGW_KEY}"}   # secret-free
matrix:
  llm.model: [model-alpha-32k, model-beta-v3]   # 2 Arms, auto-named, per-model rate bucket, shared prepare
```

## Run

`eval_harness` shares the `sdks/python/` uv project (sibling package to `e2e_harness`;
it does **not** import e2e_harness). Run from `sdks/python/`:

```bash
cd sdks/python && uv sync
# end-to-end with the echo producer (no live server) — writes real reports:
uv run python -m eval_harness.cli eval_harness/materials/experiments/smoke.yaml --mock --fresh --runs-dir /tmp/eh
# → /tmp/eh/smoke/<run-id>/{worksheet.jsonl, results.csv, report/{comparison.md, <arm_id>.md}}
# tests + lint (shared project): uv run pytest tests/  ·  make lint
```

Output `runs/<exp>/<run-id>/`: `worksheet.jsonl` (checkpoint, resumable), `results.csv`
(flat scalar projection), `report/comparison.md` (ranking + winner + per-case
deltas) and `report/<arm_id>.md` (single-Arm by-facet).

## Status

**Done + tested** (`cd sdks/python && uv run pytest tests/`, `make lint`):
model · worksheet + checkpoint · metric (base + WEIGHT + builtins + registry) ·
aggregate (weighted_overall + percentiles) · report (pivot + render) ·
schedule (per-endpoint AIMD ratelimit + reconcile) · config · engine
orchestrator · CLI. 32 tests, incl. resume (crash→reload→fill gaps),
failure isolation, retry, applies-to abstain, hash-mismatch guard.

**Pending (lives in the consumer project, not here):**
the live Provisioner/Solver (provision resource → upload+poll
sources → internal-chat SSE → answer + observations) is a consumer concern —
the consumer repo will import `eval_harness` and provide it. Run `--mock` here.

**Backlog:** LLM-as-judge metric (RAGAS-backed) · matrix sweep in yaml ·
measurement observation keys (ttft/total/tokens) finalised against real SSE.

## Layout

```
sdks/python/eval_harness/          # sibling package to sdks/python/e2e_harness/ (shared uv project)
  model/      experiment(Experiment/Arm) · evalset(EvalSet/eval_view/FacetSchema; case=common.Case) · sample(MetricResult)
  worksheet/  worksheet(Worksheet/Row/cells) · checkpoint(jsonl + async Checkpointer)
  metric/     base(BaseMetric+WEIGHT) · aggregate · builtins · registry
  schedule/   ratelimit(per-endpoint AIMD) · reconcile(ReconcileEngine)
  report/     pivot(arm_id/facet/compare) · render(md/csv)
  produce/    mock(echo)          # live producers live in the consumer repo
  config.py · engine.py(run_experiment) · cli.py
  materials/  experiments/ · cases/          # sample evalset (also bundled package data)
  ruff.toml                                  # scoped lint (line-length 100); e2e_harness untouched
sdks/python/tests/                 # eval_harness tests live here (test_{model,worksheet,…}.py)
```
