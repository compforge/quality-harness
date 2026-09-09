"""End-to-end through the public entry: load yaml → run_experiment (mock
producers, no live services) → reports written."""

from pathlib import Path

import pytest
import yaml
from harness_common import Experiment as BaseExperiment
from harness_common import ExperimentRun

import eval_harness
from eval_harness.config import _load_evalset, load_experiment
from eval_harness.engine import resolve_weights, run_experiment
from eval_harness.metric.registry import resolve
from eval_harness.schedule.ratelimit import GateRegistry
from eval_harness.schedule.reconcile import Solver, SolveResult

# materials ship inside the package (python/eval_harness/materials/)
MATERIALS = Path(eval_harness.__file__).resolve().parent / "materials"
SMOKE = MATERIALS / "experiments" / "smoke.yaml"
SHARED_CASESET = (
    Path(__file__).parents[4] / "conformance" / "case" / "fixtures" / "shared-chat.yaml"
)


class _Prov:
    async def prepare(self, key, service, evalset):
        return f"nb-{key[:6]}"

    async def clean(self, key, subject_id):
        pass


class _EchoSolver(Solver):
    """Echo ground_truth for answer cases; emit a refusal for refuse cases."""

    async def solve(self, row, service, subject_id):
        if row.expected_behavior == "refuse":
            resp = "无法从文档中找到相关信息"
        else:
            resp = row.ground_truth or ""
        return SolveResult(
            response=resp,
            retrieved=["chunk-1"],
            observations={"total_ms": 900, "ttft_ms": 80},
            meta={"message_id": f"m-{row.arm_id}-{row.case_id}"},
        )


def test_load_experiment_resolves_cases_and_facets():
    exp = load_experiment(SMOKE)
    assert isinstance(exp, BaseExperiment)
    assert exp.name == "smoke"
    assert exp.evalsets[0].caseset == "smoke"
    assert [c.id for c in exp.evalsets[0].cases] == ["f1", "f2", "r1"]
    assert len(exp.resolved_arms()) == 2
    # Facet schema comes from the canonical CaseSet and was validated by the loader.
    assert "difficulty" in exp.facet_schema().facets


def test_eval_loads_the_shared_eval_and_perf_caseset():
    evalset = _load_evalset(str(SHARED_CASESET), MATERIALS)
    assert evalset.caseset == "shared-chat"
    assert [case.id for case in evalset.cases] == ["ordinary_chat", "knowledge_chat"]
    assert evalset.facet_schema.ordered_value_lists() == {"difficulty": ["simple", "complex"]}
    assert set(evalset.cases[0].judge) == {"eval", "perf"}


def test_experiment_cannot_override_caseset_facets(tmp_path):
    raw = yaml.safe_load(SMOKE.read_text())
    raw["facets"] = {"difficulty": {"values": ["trivial"]}}
    config = tmp_path / "experiment.yaml"
    config.write_text(yaml.safe_dump(raw), encoding="utf-8")
    with pytest.raises(ValueError, match="cannot override"):
        load_experiment(config, materials_root=MATERIALS)


async def test_run_experiment_end_to_end(tmp_path):
    exp = load_experiment(SMOKE)
    metrics = resolve(exp.metrics)
    ws = await run_experiment(
        exp,
        _Prov(),
        _EchoSolver(),
        metrics,
        runs_dir=tmp_path,
        gates=GateRegistry(pause_s=0.0),
        fresh=True,
        config_path=SMOKE,
    )
    assert ws.is_complete()
    assert isinstance(ws, ExperimentRun)
    assert ws.artifact_paths() == {
        "worksheet": "worksheet.jsonl",
        "results": "results.csv",
    }
    # answer cases exact-match 1.0; refuse case keyword_refusal 1.0
    assert ws.rows[("model-alpha", "smoke", "f1")].scores["exact_match"].result.score == 1.0
    assert ws.rows[("model-alpha", "smoke", "r1")].scores["keyword_refusal"].result.score == 1.0
    # exact_match abstains on the refuse case (None, not 0)
    assert ws.rows[("model-alpha", "smoke", "r1")].scores["exact_match"].result.score is None
    # latency measurement captured
    assert ws.rows[("model-alpha", "smoke", "f1")].scores["latency"].result.value == 900

    # reports written under runs/<experiment>/<run-id>/ (run-id = experiment_hash);
    # multi-arm_id → report/ folder (comparison + per-arm_id)
    run_dir = tmp_path / "smoke" / ws.run_id
    assert (run_dir / "results.csv").is_file()
    assert (run_dir / "report" / "comparison.md").is_file()
    assert (run_dir / "report" / "model-alpha.md").is_file()
    # config snapshot travels with the run (raw source, placeholders intact)
    assert (run_dir / "experiment.yaml").read_text() == Path(SMOKE).read_text()
    assert (run_dir / "worksheet.jsonl").is_file()
    # cross-harness verdict.json lands beside the reports
    assert (run_dir / "verdict.json").is_file()


def test_single_env_writes_flat_report_md(tmp_path):
    from eval_harness.engine import write_reports
    from eval_harness.model.evalset import EvalSet
    from eval_harness.model.experiment import Experiment, Service
    from eval_harness.tests.eval_cases import make_eval_case
    from eval_harness.worksheet.worksheet import Worksheet

    exp = Experiment(
        name="solo",
        service=Service(name="chat"),
        evalsets=[EvalSet(caseset="c", cases=[make_eval_case(id="q1", query="q")])],
        metrics=[],
    )
    out = write_reports(Worksheet.build(exp), exp, [], tmp_path / "solo")
    # single arm_id → flat report.md, no report/ folder
    assert (tmp_path / "solo" / "report.md").is_file()
    assert not (tmp_path / "solo" / "report").exists()
    assert out.name == "report.md"


async def test_resume_via_run_experiment(tmp_path):
    exp = load_experiment(SMOKE)
    metrics = resolve(exp.metrics)

    # first pass: solver fails everything → solves FAILED, incomplete
    class _Boom(Solver):
        calls = 0

        async def solve(self, row, service, subject_id):
            type(self).calls += 1
            raise RuntimeError("down")

    ws1 = await run_experiment(
        exp,
        _Prov(),
        _Boom(),
        metrics,
        runs_dir=tmp_path,
        gates=GateRegistry(pause_s=0.0),
        fresh=True,
    )
    assert not ws1.is_complete()

    # second pass: healthy solver, resume from checkpoint → only gaps filled
    solver = _EchoSolver()
    ws2 = await run_experiment(
        exp,
        _Prov(),
        solver,
        metrics,
        runs_dir=tmp_path,
        gates=GateRegistry(pause_s=0.0),
    )
    assert ws2.is_complete()


def test_resolve_weights_excludes_measure():
    exp = load_experiment(SMOKE)
    w = resolve_weights(exp, resolve(exp.metrics))
    assert "latency" not in w  # measurement metric not weighted
    assert w["exact_match"] == 1.0


async def test_run_id_defaults_to_experiment_hash(tmp_path):
    exp = load_experiment(SMOKE)
    ws = await run_experiment(
        exp,
        _Prov(),
        _EchoSolver(),
        resolve(exp.metrics),
        runs_dir=tmp_path,
        gates=GateRegistry(pause_s=0.0),
        fresh=True,
    )
    assert ws.run_id == exp.experiment_hash()  # default reuse namespace = the experiment hash
    assert all(r.run_id == ws.run_id for r in ws.rows.values())
