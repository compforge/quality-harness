import json

import yaml
from harness_common import Experiment as BaseExperiment
from harness_common import ExperimentRun

from perf_harness.drive.load import LoadPlan
from perf_harness.drive.runner import MockRunner, Runner
from perf_harness.engine import Engine, Experiment
from perf_harness.model import (
    Outcome,
    ResourceProfile,
    Service,
    make_run_id,
)
from perf_harness.report import write_run


def _subject() -> Service:
    return Service("chat", base_url="http://127.0.0.1:0")


def test_make_run_id_format():
    rid = make_run_id()
    assert len(rid) == len("20260101-000000")  # YYYYMMDD-HHMMSS


async def test_write_run_lays_out_experiment_dir(tmp_path):
    experiment = Experiment(
        service=_subject(),
        runner=MockRunner(base_ms=2),
        resources=[ResourceProfile(workers=2)],
        loads=[LoadPlan(request_rate=float("inf"), max_concurrency=2, duration_s=(0.0 + 0.2))],
        name="chat-sizing",
    )
    engine = Engine(experiment, run_id="20260101-000000")
    run = await engine.run()
    assert isinstance(experiment, BaseExperiment)
    assert isinstance(run, ExperimentRun)
    assert run.experiment == "chat-sizing" and run.run_id == "20260101-000000"
    config_dir = tmp_path / "source"
    config_dir.mkdir()
    (config_dir / "cases.yaml").write_text("caseset: shared\ncases:\n  - {id: simple, input: {}}\n")
    config = config_dir / "experiment.yaml"
    config.write_text("caseset: ./cases.yaml\n")
    write_run(run, str(tmp_path), config_path=str(config))
    assert run.artifact_paths() == {
        "model": "run.json",
        "requests": "requests.jsonl",
        "evaluations": "evaluations.json",
        "timeseries": "timeseries.csv",
    }

    run_dir = tmp_path / "chat-sizing" / "20260101-000000"
    assert run_dir.is_dir()
    for f in ("report.md", "summary.csv", "by_facet.csv", "timeseries.csv", "run.json"):
        assert (run_dir / f).exists()

    meta = json.loads((run_dir / "run.json").read_text())
    assert meta["experiment"] == "chat-sizing"
    assert meta["run_id"] == "20260101-000000"
    assert len(meta["executions"]) == len(run.arm_runs)

    snapshot = yaml.safe_load((run_dir / "config.yaml").read_text())
    assert snapshot["caseset"] == "./caseset.yaml"
    assert (run_dir / "caseset.yaml").read_text() == (config_dir / "cases.yaml").read_text()

    # run.jsonl at the experiment level, one line per run (accumulates)
    log = tmp_path / "chat-sizing" / "run.jsonl"
    assert log.exists()
    assert len(log.read_text().splitlines()) == 1


async def test_run_id_reaches_fire(tmp_path):
    seen: list[str] = []

    class RecordingRunner(Runner):
        name = "rec"

        async def fire(self, ctx):
            seen.append(ctx.arm_run.run_id)
            return Outcome(ok=True, status=200, duration_ms=1.0)

    experiment = Experiment(
        service=_subject(),
        runner=RecordingRunner(),
        resources=[ResourceProfile()],
        loads=[LoadPlan(request_rate=float("inf"), max_concurrency=1, duration_s=(0.0 + 0.1))],
    )
    await Engine(experiment, run_id="RID-123").run()
    assert seen and all(r == "RID-123" for r in seen)
