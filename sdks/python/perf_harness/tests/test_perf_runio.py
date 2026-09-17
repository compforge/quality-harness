"""runio — the model layer on disk: run.json full serialization, requests.jsonl raw
layer, and load_run/load_outcomes reconstructing the model (and thus a working
MetricStore) offline. The report/CSVs are derived views; THESE are the analysis
contract, so the round-trip must be lossless for everything the store can address."""

import json
import shutil
from pathlib import Path

import pytest
from spec_case.model import Case

from perf_harness.drive.load import LoadPlan
from perf_harness.drive.runner import Runner
from perf_harness.engine import Engine, Experiment
from perf_harness.metric import series_id
from perf_harness.metric.store import MetricStore
from perf_harness.model import Outcome, ResourceProfile, Service, SloAssertion, WindowSelector
from perf_harness.observe import FamilySpec, Probe
from perf_harness.report import write_run
from perf_harness.runio import RUN_SCHEMA, load_run
from perf_harness.slo import evaluate_slo

PERF_FIXTURES = Path(__file__).parents[4] / "conformance" / "perf" / "fixtures"


class _FakeTop(Probe):
    """Constant gauges with a service label — exercises labeled-series round-trip."""

    name = "top.chat"
    source = "k8s"
    _service = "chat"
    families = {"cpu_m": FamilySpec("millicores")}

    @property
    def family(self) -> str:
        return "top"

    async def sample(self, ctx):
        return {"cpu_m": 123.0}


class _WL(Runner):
    name = "w"

    async def fire(self, ctx):
        return Outcome(status=200, duration_ms=10.0, events=1, metrics={"first_byte_ms": 5.0})


async def _run(tmp_path):
    exp = Experiment(
        service=Service("chat", base_url="http://127.0.0.1:0"),
        runner=_WL(),
        resources=[ResourceProfile(workers=2, memory="2Gi")],
        loads=[
            LoadPlan(
                request_rate=float("inf"),
                max_concurrency=2,
                duration_s=(0.0 + 0.3),
                abort_on_error_rate=0.5,
                breaker_min_n=5,
            )
        ],
        probes=[_FakeTop()],
        cases=[Case(id="a", input={}, facets={"difficulty": "simple"})],
        slo=[
            SloAssertion(metric="error_rate", op="lt", threshold=0.5),
            SloAssertion(metric="nonexistent_ms.p95", op="lt", threshold=1.0),  # → skipped
            SloAssertion(
                metric='top.cpu_m{service="chat"}.last',
                op="lte",
                threshold=123.0,
                window=WindowSelector(kind="cooldown"),
            ),
        ],
        observe_interval_s=0.1,
        cooldown_s=0.12,
        name="rt",
    )
    run = await Engine(exp, run_id="20260101-000000").run()
    write_run(run, str(tmp_path))
    return run, tmp_path / "rt" / "20260101-000000"


async def test_run_json_is_the_full_model(tmp_path):
    run, run_dir = await _run(tmp_path)
    doc = json.loads((run_dir / "run.json").read_text())
    assert doc["schema"] == RUN_SCHEMA
    t = doc["executions"][0]
    # everything the live model knows is on disk: identity, config, verdicts, metadata
    assert t["id"] == run.arm_runs[0].label()
    assert t["arm"]["resources"]["workers"] == 2
    assert t["arm"]["resources"]["memory"] == "2Gi"
    assert t["arm"]["load"]["abort_on_error_rate"] == 0.5
    assert t["arm"]["load"]["breaker_min_n"] == 5
    assert t["stop"]["reason"] == "deadline"
    states = {(c["metric"], c["window"]["kind"]): c["state"] for c in t["slo"]}
    assert states == {
        ("error_rate", "measurement"): "pass",
        ("nonexistent_ms.p95", "measurement"): "skipped",
        ('top.cpu_m{service="chat"}.last', "cooldown"): "pass",
    }
    windows = {window["id"]: window for window in t["windows"]}
    assert "cooldown" in windows
    assert t["registry"]["first_byte_ms"]["unit"] == "ms"  # metric metadata persisted
    assert windows["measurement"]["request"]["n"] == run.arm_runs[0].measurement.request.n
    assert "a" in windows["measurement"]["by_case"]
    assert "simple" in windows["measurement"]["by_facet"]["difficulty"]
    sid = series_id("top.cpu_m", {"service": "chat"})
    assert windows["measurement"]["probe_metrics"][sid]["kind"] == "gauge"
    assert windows["measurement"]["probe_metrics"][sid]["peak"] == 123.0


async def test_load_run_round_trips_the_store(tmp_path):
    run, run_dir = await _run(tmp_path)
    loaded = load_run(run_dir)
    assert loaded.run_id == run.run_id and len(loaded.arm_runs) == 1
    assert loaded.artifact_paths() == {
        "model": "run.json",
        "requests": "requests.jsonl",
        "evaluations": "evaluations.json",
        "timeseries": "timeseries.csv",
    }
    live, offline = run.arm_runs[0], loaded.arm_runs[0]
    # the offline MetricStore answers the SAME addressed reads as the live one
    refs = [
        "request.error_rate.value",
        "request.duration_ms.p99",
        "first_byte_ms.p95",
        'duration_ms{difficulty="simple"}.p50',
        'top.cpu_m{service="chat"}.peak',
    ]
    ls, os_ = MetricStore(run.arm_runs), MetricStore(loaded.arm_runs)
    for ref in refs:
        assert ls.query(live, ref) == os_.query(offline, ref), ref
    # series came back from timeseries.csv with the family's unit
    sid = series_id("top.cpu_m", {"service": "chat"})
    assert offline.series[sid].unit == "millicores"
    assert [s.value for s in offline.series[sid].samples] == [
        s.value for s in live.series[sid].samples
    ]
    assert [s.t for s in offline.series[sid].samples] == [s.t for s in live.series[sid].samples]
    assert offline.metrics["top.cpu_m"].labels == live.metrics["top.cpu_m"].labels
    # stop/slo verdicts survive (offline SLO re-reads agree)
    assert offline.stop == live.stop
    assert [(w.id, w.start_s, w.end_s) for w in offline.windows] == [
        (w.id, w.start_s, w.end_s) for w in live.windows
    ]
    assert [c.state for c in offline.slo] == [c.state for c in live.slo]
    assert [c.assertion.window for c in offline.slo] == [c.assertion.window for c in live.slo]
    assert evaluate_slo(offline, [offline.slo[-1].assertion])[0].passed


async def test_request_facts_and_evaluations_round_trip_separately(tmp_path):
    run, directory = await _run(tmp_path)
    live = run.arm_runs[0]
    loaded = load_run(directory).arm_runs[0]
    assert loaded.requests == live.requests
    assert loaded.evaluations == live.evaluations
    assert [c.outcome for c in loaded.operation_runs] == [c.outcome for c in live.operation_runs]
    assert len(loaded.requests) == len(live.operation_runs)
    rows = [json.loads(line) for line in (directory / "requests.jsonl").read_text().splitlines()]
    assert all("ok" not in row["operation_run"]["outcome"] for row in rows)
    assert all("evaluation" not in row for row in rows)


def test_reads_language_neutral_conformance_fixture(tmp_path):
    for source, target in [
        ("basic.run.json", "run.json"),
        ("basic.requests.jsonl", "requests.jsonl"),
        ("basic.evaluations.json", "evaluations.json"),
    ]:
        shutil.copy(PERF_FIXTURES / source, tmp_path / target)
    run = load_run(tmp_path, with_series=False)
    call = run.arm_runs[0].operation_runs[0]
    assert call.outcome.meta["trace_id"] == "0123456789abcdef0123456789abcdef"
    assert run.arm_runs[0].requests[1].state == "dropped"


async def test_load_run_rejects_unknown_schema(tmp_path):
    _, run_dir = await _run(tmp_path)
    doc = json.loads((run_dir / "run.json").read_text())
    doc["schema"] = 999
    (run_dir / "run.json").write_text(json.dumps(doc))
    with pytest.raises(ValueError, match="schema"):
        load_run(run_dir)


async def test_cli_report_rerenders_from_model_layer(tmp_path):
    # the rendering is a pure downstream of the model layer: delete the html, re-render
    # from run.json/timeseries.csv alone — no engine, no re-press
    from perf_harness import cli

    _, run_dir = await _run(tmp_path)
    (run_dir / "report.html").unlink()
    assert cli.main(["report", str(run_dir)]) == 0
    assert (run_dir / "report.html").exists()


async def test_probe_errors_round_trip(tmp_path):
    # observation-failure census survives the disk round-trip: an offline reader can
    # tell "broken observability" from "calm data" without the live process
    from perf_harness.observe import Probe

    class _Down(Probe):
        name = "metrics.chat"
        source = "http"
        _service = "chat"
        families = {"x": FamilySpec("count")}

        @property
        def family(self):
            return "metrics"

        async def sample(self, ctx):
            raise RuntimeError("endpoint down")

    exp = Experiment(
        service=Service("chat", base_url="http://127.0.0.1:0"),
        runner=_WL(),
        resources=[ResourceProfile()],
        loads=[LoadPlan(request_rate=float("inf"), max_concurrency=1, duration_s=(0.0 + 0.2))],
        probes=[_Down()],
        observe_interval_s=0.05,
        name="pe",
    )
    run = await Engine(exp, run_id="20260101-000001").run()
    write_run(run, str(tmp_path))
    loaded = load_run(tmp_path / "pe" / "20260101-000001")
    pe = loaded.arm_runs[0].probe_errors["metrics.chat"]
    assert pe.failures >= 1 and pe.ticks >= pe.failures and "down" in pe.last


async def test_setup_error_still_writes_complete_run_artifacts(tmp_path):
    class _BrokenSetup(Runner):
        async def setup(self, ctx):
            raise RuntimeError("target unavailable")

        async def fire(self, ctx):
            return Outcome(status=200, duration_ms=1.0)

    exp = Experiment(
        service=Service("chat", base_url="http://127.0.0.1:0"),
        runner=_BrokenSetup(),
        resources=[ResourceProfile()],
        loads=[LoadPlan(request_rate=float("inf"), max_concurrency=1, duration_s=(0.0 + 0.1))],
        name="setup-error",
    )
    run = await Engine(exp, run_id="20260101-000002").run()
    paths = write_run(run, str(tmp_path))
    run_dir = Path(paths["run_dir"])

    assert {"run.json", "requests.jsonl", "report.md", "verdict.json"} <= {
        path.name for path in run_dir.iterdir()
    }
    doc = json.loads((run_dir / "run.json").read_text())
    assert doc["passed"] is False
    assert doc["executions"][0]["phase_errors"] == [
        {"phase": "setup", "error_type": "RuntimeError", "message": "target unavailable"}
    ]
    loaded = load_run(run_dir)
    assert loaded.arm_runs[0].phase_errors == run.arm_runs[0].phase_errors

    verdict = json.loads((run_dir / "verdict.json").read_text())
    assert verdict["status"] == "error"
    assert "setup: RuntimeError: target unavailable" in verdict["reason"]
    assert "ERROR" in (run_dir / "report.md").read_text()


async def test_model_and_verdict_survive_report_renderer_failure(tmp_path, monkeypatch):
    from perf_harness.report import render

    run, _ = await _run(tmp_path / "baseline")

    def broken_report(*args, **kwargs):
        raise RuntimeError("renderer failed")

    monkeypatch.setattr(render, "write_report", broken_report)
    with pytest.raises(RuntimeError, match="renderer failed"):
        write_run(run, str(tmp_path / "failed-render"))

    run_dir = tmp_path / "failed-render" / run.experiment / run.run_id
    assert {"run.json", "requests.jsonl", "timeseries.csv", "verdict.json"} <= {
        path.name for path in run_dir.iterdir()
    }


async def test_reader_accepts_optional_fields_and_null_call(tmp_path):
    run, directory = await _run(tmp_path)
    path = directory / "requests.jsonl"
    rows = [json.loads(line) for line in path.read_text().splitlines()]
    rows[0]["request"]["future_optional"] = "ignored"
    dropped = dict(
        rows[0]["request"],
        id="dropped-fixture",
        operation_run_id=None,
        dispatched_at=None,
        state="dropped",
        reason="concurrency_limit",
    )
    rows.append({"arm_run_id": run.arm_runs[0].id, "request": dropped, "operation_run": None})
    path.write_text("".join(json.dumps(row) + "\n" for row in rows))
    restored = load_run(directory)
    assert restored.arm_runs[0].requests[-1].state == "dropped"
    assert len(restored.arm_runs[0].operation_runs) == len(run.arm_runs[0].operation_runs)
