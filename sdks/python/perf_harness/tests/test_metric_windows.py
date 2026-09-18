import asyncio
import csv
import time
from pathlib import Path

import httpx
import pytest
import yaml
from harness_common import ClientManager
from harness_toolbox.prometheus import PrometheusClient, PrometheusDataSource

from perf_harness import (
    Engine,
    Experiment,
    LoadPlan,
    MetricProbe,
    Outcome,
    ResourceProfile,
    Service,
)
from perf_harness.config import load_experiment
from perf_harness.drive.load import Warmup
from perf_harness.drive.runner import Runner
from perf_harness.metric import Missing
from perf_harness.metric.store import MetricStore
from perf_harness.model import ReportColumn, WindowSelector
from perf_harness.observe import ProbeContext, WindowQuery
from perf_harness.report import write_report, write_run
from perf_harness.runio import load_run


async def test_baseline_precedes_load_final_scrape_includes_cooldown_and_report_is_offline(
    monkeypatch, tmp_path
):
    baseline = False
    completed = 0
    clients = []
    scrape_counts = []

    async def handle(request):
        nonlocal baseline
        assert request.url.path == "/metric"
        assert request.headers["Authorization"] == "metrics-secret"
        if not baseline:
            await asyncio.sleep(0.02)
            baseline = True
        scrape_counts.append(completed)
        return httpx.Response(
            200,
            text=(
                f"duration_seconds_sum {completed * 10}\n"
                f"duration_seconds_count {completed}\n"
                f"ttft_seconds_sum {completed * 2}\n"
                f"ttft_seconds_count {completed}\n"
            ),
        )

    def create(source, owner):
        client = PrometheusClient(source, clients=owner, transport=httpx.MockTransport(handle))
        clients.append(client)
        return client

    monkeypatch.setattr(PrometheusDataSource, "create_client", create)

    class Slow(Runner):
        async def fire(self, ctx):
            nonlocal completed
            assert baseline
            await asyncio.sleep(0.06)
            completed += 1
            return Outcome(status=200, duration_ms=60)

        async def cleanup(self, ctx):
            assert scrape_counts[-1] == completed
            assert clients[0]._runtime is not None

    probe = MetricProbe(
        service="chat",
        path="/metric",
        summaries=[
            WindowQuery(
                "duration_mean_s",
                "sum(increase(duration_seconds_sum[$window])) / sum(increase(duration_seconds_count[$window]))",
                unit="s",
            ),
            WindowQuery(
                "ttft_mean_s",
                "sum(increase(ttft_seconds_sum[$window])) / sum(increase(ttft_seconds_count[$window]))",
                unit="s",
            ),
        ],
    )
    columns = [
        ReportColumn("error rate", "request.error_rate.value"),
        ReportColumn(
            "duration (s)",
            'metric.duration_mean_s{service="chat"}.value',
            WindowSelector("observation"),
        ),
        ReportColumn(
            "TTFT (s)", 'metric.ttft_mean_s{service="chat"}.value', WindowSelector("observation")
        ),
    ]
    run = await Engine(
        Experiment(
            service=Service(
                "chat", base_url="http://chat", headers={"Authorization": "metrics-secret"}
            ),
            runner=Slow(),
            resources=[ResourceProfile()],
            loads=[
                LoadPlan(
                    request_rate=float("inf"),
                    max_inflight=2,
                    hold_s=0.04,
                    warmup=Warmup(step_s=0),
                    cooldown_timeout_s=0.2,
                )
            ],
            probes=[probe],
            observe_interval_s=0.01,
            report_columns=columns,
        )
    ).run()
    arm = run.arm_runs[0]
    assert run.passed and not arm.probe_errors and not arm.phase_errors
    assert completed == 2 and scrape_counts[0] == 0 and scrape_counts[-1] == 2
    assert arm.measurement.duration_s == pytest.approx(0.04)
    assert all(client._http.is_closed for client in clients)
    observation = next(w for w in arm.windows if w.kind == "observation")
    store = MetricStore([arm])
    assert store.query(arm, columns[1].metric, observation) == pytest.approx(10)
    assert store.query(arm, columns[2].metric, observation) == pytest.approx(2)
    assert isinstance(store.query(arm, columns[1].metric), Missing)
    assert all("$window" not in result.expression for result in arm.window_observations)
    assert all(result.end_ms > result.start_ms for result in arm.window_observations)
    paths = write_run(run, str(tmp_path))
    assert "metrics-secret" not in Path(paths["run.json"]).read_text()
    restored = load_run(paths["run_dir"])
    assert restored.report_columns == columns
    assert restored.arm_runs[0].window_observations == arm.window_observations

    def no_remote_reads(*args, **kwargs):
        raise AssertionError("offline report accessed a client")

    monkeypatch.setattr(PrometheusDataSource, "create_client", no_remote_reads)
    offline = write_report(
        restored.arm_runs, str(tmp_path / "offline"), columns=restored.report_columns
    )
    with Path(offline["summary"]).open() as stream:
        rows = list(csv.reader(stream))
    assert rows[0][-3:] == ["error rate", "duration (s)", "TTFT (s)"]
    assert rows[1][-3:] == ["0", "10", "2"]
    assert "TTFT (s)" in Path(offline["report_html"]).read_text()
    assert "duration (s)" in Path(offline["report"]).read_text()


async def test_window_summary_preserves_missing_on_zero_denominator(monkeypatch):
    now = 1000000
    monkeypatch.setattr("prombed.prombed._now_ms", lambda: now)
    monkeypatch.setattr(
        PrometheusDataSource,
        "create_client",
        lambda source, owner: PrometheusClient(
            source,
            clients=owner,
            transport=httpx.MockTransport(
                lambda request: httpx.Response(200, text="x_sum 0\nx_count 0\n")
            ),
        ),
    )
    probe = MetricProbe(
        summaries=[
            WindowQuery("mean", "sum(increase(x_sum[$window])) / sum(increase(x_count[$window]))")
        ]
    )
    async with ClientManager() as clients:
        ctx = ProbeContext(
            Service("chat", base_url="http://chat"), None, time.monotonic(), clients=clients
        )
        await probe.sample(ctx)
        now += 1000
        await probe.sample(ctx)
        from perf_harness.model import Window

        result = (
            await probe.finish(
                ctx, [Window("observation", "observation", "observation", 0, 1, True)]
            )
        )[0]
        assert result.values == {} and result.error is not None


def test_config_defaults_to_direct_scrape_and_selects_window_results(tmp_path):
    config = {
        "service": {"name": "chat", "base_url": "http://chat"},
        "runner": {"name": "mock"},
        "load": {"request_rate": 8, "max_inflight": 80, "hold_s": 1},
        "observe": [
            {
                "name": "chat",
                "probes": [
                    {
                        "name": "metric",
                        "path": "/metric",
                        "summaries": [
                            {
                                "name": "mean_s",
                                "promql": "sum(increase(d_sum[$window])) / sum(increase(d_count[$window]))",
                                "unit": "s",
                            }
                        ],
                    }
                ],
            }
        ],
        "report": {
            "columns": [
                {
                    "title": "average",
                    "metric": 'metric.mean_s{service="chat"}.value',
                    "window": {"kind": "observation"},
                }
            ]
        },
    }
    path = tmp_path / "perf.yaml"
    path.write_text(yaml.safe_dump(config))
    experiment, _ = load_experiment(str(path), mock=True)
    probe = next(p for p in experiment.probes if isinstance(p, MetricProbe))
    assert probe._path == "/metric" and probe.summaries[0].window.kind == "observation"
    assert experiment.report_columns[0].title == "average"
    config["report"]["columns"][0]["metric"] = 'metric.typo{service="chat"}.value'
    path.write_text(yaml.safe_dump(config))
    with pytest.raises(ValueError, match="declared metric"):
        load_experiment(str(path), mock=True)


async def test_cancel_during_baseline_cancels_scrape_and_runs_cleanup(monkeypatch):
    entered, closed, cleaned = asyncio.Event(), asyncio.Event(), asyncio.Event()

    async def handler(request):
        entered.set()
        try:
            await asyncio.Event().wait()
        finally:
            closed.set()

    monkeypatch.setattr(
        PrometheusDataSource,
        "create_client",
        lambda source, clients: PrometheusClient(
            source, clients=clients, transport=httpx.MockTransport(handler)
        ),
    )

    class NoFire(Runner):
        async def fire(self, ctx):
            raise AssertionError("must not send before baseline completes")

        async def cleanup(self, ctx):
            cleaned.set()

    run = asyncio.create_task(
        Engine(
            Experiment(
                service=Service("chat", base_url="http://chat"),
                runner=NoFire(),
                resources=[ResourceProfile()],
                loads=[LoadPlan(request_rate=1, max_inflight=1, hold_s=1)],
                probes=[MetricProbe(summaries=[WindowQuery("count", "sum(x)")])],
            )
        ).run()
    )
    await asyncio.wait_for(entered.wait(), 1)
    run.cancel()
    with pytest.raises(asyncio.CancelledError):
        await asyncio.wait_for(run, 1)
    assert closed.is_set() and cleaned.is_set()
