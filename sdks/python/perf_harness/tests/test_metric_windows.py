"""Post-load history, raw evidence and retry stay independent of request execution."""

import asyncio
import json
from pathlib import Path
from urllib.parse import parse_qs

import httpx
import pytest
from harness_toolbox.http import HTTPClient, HTTPClientProvider
from harness_toolbox.prometheus import PrometheusDataSource

from perf_harness import Engine, Experiment, LoadPlan, Outcome, ResourceProfile, Service, Warmup
from perf_harness.drive.runner import Runner
from perf_harness.metric.store import MetricStore
from perf_harness.model import ReportColumn, WindowSelector
from perf_harness.observe import PrometheusQuery, PrometheusQueryProbe, WindowQuery
from perf_harness.observe.history import recollect_metrics
from perf_harness.report import write_report, write_run
from perf_harness.runio import load_run


def with_transport(monkeypatch, handler):
    pools = []

    def create(provider, _):
        client = HTTPClient(transport=httpx.MockTransport(handler), trust_env=False)
        pools.append(client)
        return client

    monkeypatch.setattr(HTTPClientProvider, "create_client", create)
    return pools


def response(request, *, value="2.5", labels=None, **annotations):
    form = parse_qs(request.content.decode())
    if request.url.path.endswith("query_range"):
        data = {
            "resultType": "matrix",
            "result": [{"metric": labels or {}, "values": [[float(form["start"][0]), value]]}],
        }
    else:
        data = {
            "resultType": "vector",
            "result": [{"metric": labels or {}, "value": [float(form["time"][0]), value]}],
        }
    return httpx.Response(200, json={"status": "success", "data": data, **annotations})


class Slow(Runner):
    def __init__(self):
        self.completed = 0
        self.cleaned = False

    async def fire(self, ctx):
        await asyncio.sleep(0.04)
        self.completed += 1
        return Outcome(status=200, duration_ms=40)

    async def cleanup(self, ctx):
        self.cleaned = True


def experiment(runner):
    return Experiment(
        service=Service("api", base_url="http://api", headers={"Authorization": "load-secret"}),
        runner=runner,
        resources=[ResourceProfile()],
        loads=[
            LoadPlan(
                request_rate=float("inf"),
                max_inflight=2,
                hold_s=0.02,
                warmup=Warmup(step_s=0),
                cooldown_timeout_s=1,
            )
        ],
        probes=[
            PrometheusQueryProbe(
                data_source=PrometheusDataSource(
                    "http://prom/proxy", headers={"X-Prom-Token": "metric-secret"}
                ),
                service="api",
                step_s=0.01,
                queries=[PrometheusQuery("active", "sum(active)")],
                summaries=[WindowQuery("count", "sum(increase(requests_total[$window]))")],
            )
        ],
        report_columns=[
            ReportColumn(
                "count",
                'prometheus_query.count{service="api"}.value',
                WindowSelector("full"),
            )
        ],
    )


async def test_only_queries_after_cleanup_and_reloads_without_io(monkeypatch, tmp_path):
    runner = Slow()
    requests = []

    def handle(request):
        assert runner.cleaned and runner.completed == 2
        assert request.url.path in {"/proxy/api/v1/query", "/proxy/api/v1/query_range"}
        assert "Authorization" not in request.headers
        assert request.headers["X-Prom-Token"] == "metric-secret"
        requests.append(request)
        return response(request, infos=["server annotation"])

    pools = with_transport(monkeypatch, handle)
    exp = experiment(runner)
    run = await Engine(exp).run()
    arm = run.arm_runs[0]
    assert run.status == "completed" and not arm.probe_errors
    assert len(requests) == 2 and all(pool.is_closed for pool in pools)
    hold = next(w for w in arm.windows if w.phase == "hold")
    assert hold.duration_s == pytest.approx(0.02)
    observation = next(w for w in arm.windows if w.kind == "full")
    query = arm.prometheus_observations[1]
    assert query.start_s == pytest.approx(arm.started_at_s + observation.start_s, abs=0.001)
    assert query.end_s == pytest.approx(arm.started_at_s + observation.end_s, abs=0.001)
    assert query.end_s > arm.started_at_s + hold.end_s
    assert "$window" not in query.expression and query.infos == ("server annotation",)
    assert query.data and query.collected_at_s and not query.error
    paths = write_run(run, str(tmp_path))
    assert "secret" not in Path(paths["run.json"]).read_text()
    restored = load_run(paths["run_dir"])
    assert restored.arm_runs[0].prometheus_observations == arm.prometheus_observations
    assert restored.arm_runs[0].started_at_s == arm.started_at_s
    store = MetricStore(restored.arm_runs)
    ref = exp.report_columns[0].metric
    assert store.query(restored.arm_runs[0], ref, observation) == 2.5
    assert store.query(restored.arm_runs[0], ref) == 2.5
    monkeypatch.setattr(
        HTTPClientProvider,
        "create_client",
        lambda *_: pytest.fail("offline report accessed network"),
    )
    write_report(restored.arm_runs, str(tmp_path / "offline"), columns=restored.report_columns)


@pytest.mark.parametrize(
    "state", ["timeout", "http", "empty", "warning", "nan", "labels", "duplicate"]
)
async def test_incomplete_queries_remain_missing_and_do_not_change_request_results(
    monkeypatch, state
):
    def handle(request):
        if state == "timeout":
            raise httpx.ReadTimeout("timeout")
        if state == "http":
            return httpx.Response(503)
        result = response(
            request,
            value="NaN" if state == "nan" else "2.5",
            labels={"unexpected": "label"} if state == "labels" else {},
            **({"warnings": ["partial"]} if state == "warning" else {}),
        )
        payload = result.json()
        if state == "empty":
            payload["data"]["result"] = []
        elif state == "duplicate":
            payload["data"]["result"] *= 2
        return httpx.Response(200, json=payload)

    with_transport(monkeypatch, handle)
    run = await Engine(experiment(Slow())).run()
    arm = run.arm_runs[0]
    assert run.status == "completed" and len(arm.requests) == 2
    assert arm.probe_errors and all(q.error for q in arm.prometheus_observations)
    assert not arm.series and not any(w.probe_metrics for w in arm.windows)
    if state == "warning":
        assert all(q.warnings == ("partial",) for q in arm.prometheus_observations)
    if state == "nan":
        assert all(q.data for q in arm.prometheus_observations)


async def test_retry_reuses_exact_windows_preserves_failed_attempt_and_never_loads_runner(
    monkeypatch, tmp_path
):
    requests = []
    failing = True

    def handle(request):
        requests.append((request.url.path, parse_qs(request.content.decode())))
        return httpx.Response(503) if failing else response(request)

    with_transport(monkeypatch, handle)
    run = await Engine(experiment(Slow()), run_id="retry").run()
    paths = write_run(run, str(tmp_path))
    directory = Path(paths["run_dir"])
    # Unknown additive producer fields and original formatting must survive recollection.
    raw_request = (directory / "requests.jsonl").read_text().splitlines()
    extra = json.loads(raw_request[0])
    extra["producer_metadata"] = {"preserve": True}
    raw_request[0] = json.dumps(extra)
    (directory / "requests.jsonl").write_text("\n".join(raw_request) + "\n")
    doc = json.loads((directory / "run.json").read_text())
    doc["producer_metadata"] = {"preserve": True}
    doc["executions"][0]["windows"][0]["producer_metadata"] = "retained"
    (directory / "run.json").write_text(json.dumps(doc))
    original_requests = (directory / "requests.jsonl").read_bytes()
    original_evaluations = (directory / "evaluations.json").read_bytes()
    config = tmp_path / "access.yaml"
    config.write_text(
        "prometheus: {url: http://prom/proxy}\nextensions: [does_not_exist]\nrunner: {name: does_not_exist}\n"
    )
    failing = False
    retried = await recollect_metrics(str(directory), str(config))
    assert requests[:2] == requests[2:]
    doc = json.loads((directory / "run.json").read_text())
    assert doc["producer_metadata"] == {"preserve": True}
    assert doc["executions"][0]["windows"][0]["producer_metadata"] == "retained"

    assert not retried.arm_runs[0].probe_errors
    assert retried.status == run.status
    assert (directory / "requests.jsonl").read_bytes() == original_requests
    assert (directory / "evaluations.json").read_bytes() == original_evaluations
    attempts = sorted((directory / "metric-collections").glob("*.json"))
    assert len(attempts) == 2
    first = json.loads(attempts[0].read_text())
    assert all(q["error"] for q in first["executions"][run.arm_runs[0].id])
    restored = load_run(directory)
    assert all(q.data and not q.error for q in restored.arm_runs[0].prometheus_observations)
    # A later failed recollection must remove stale successful projections.
    failing = True
    failed = await recollect_metrics(str(directory), str(config))
    assert failed.arm_runs[0].probe_errors and not failed.arm_runs[0].series
    assert not any(w.probe_metrics for w in failed.arm_runs[0].windows)


async def test_original_lifecycle_error_survives_successful_collection(monkeypatch):
    class BrokenCleanup(Slow):
        async def cleanup(self, ctx):
            raise RuntimeError("cleanup failed")

    with_transport(monkeypatch, response)
    run = await Engine(experiment(BrokenCleanup())).run()
    assert run.status == "error"
    assert run.arm_runs[0].phase_errors[0].phase == "cleanup"
    assert all(q.data and not q.error for q in run.arm_runs[0].prometheus_observations)


def test_same_endpoint_cannot_silently_override_access_options():
    exp = experiment(Slow())
    exp.probes.append(
        PrometheusQueryProbe(
            data_source=PrometheusDataSource(
                "http://prom/proxy", headers={"X-Prom-Token": "different"}
            ),
            service="worker",
            queries=[PrometheusQuery("active", "sum(active)")],
        )
    )
    with pytest.raises(ValueError, match="share access options"):
        exp.__post_init__()


@pytest.mark.parametrize("failed", [True, False])
def test_collect_command_uses_saved_queries_and_reports_query_exit_status(
    monkeypatch, tmp_path, failed
):
    from perf_harness import cli

    with_transport(monkeypatch, response)
    run = asyncio.run(Engine(experiment(Slow())).run())
    paths = write_run(run, str(tmp_path))
    config = tmp_path / "access.yaml"
    config.write_text("prometheus: {url: http://prom/proxy}\n")
    with_transport(monkeypatch, (lambda _: httpx.Response(503)) if failed else response)
    monkeypatch.setattr(
        cli, "load_experiment", lambda *_: pytest.fail("recollection loaded experiment")
    )
    assert cli.main(["collect-metrics", paths["run_dir"], "--config", str(config)]) == (
        1 if failed else 0
    )


def test_cli_persists_requests_and_query_plan_before_collection(monkeypatch, tmp_path):
    from perf_harness import cli

    config = tmp_path / "config.yaml"
    config.write_text("prometheus: {url: http://prom/proxy}\n")
    exp = experiment(Slow())
    monkeypatch.setattr(cli, "load_experiment", lambda *a, **kw: (exp, str(tmp_path)))

    async def interrupted(self, run):
        directory = tmp_path / exp.name / "checkpoint"
        saved = load_run(directory)
        assert len(saved.arm_runs[0].requests) == 2
        assert len(saved.arm_runs[0].prometheus_observations) == 2
        assert (directory / "config.yaml").exists()
        raise RuntimeError("collection interrupted")

    monkeypatch.setattr(Engine, "collect_metrics", interrupted)
    with pytest.raises(RuntimeError, match="collection interrupted"):
        cli.main(["run", str(config), "--run-id", "checkpoint"])
