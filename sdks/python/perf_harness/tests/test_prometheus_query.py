import asyncio
import time
from urllib.parse import parse_qs

import httpx
import pytest
from harness_common.client import ClientManager
from harness_toolbox.errors import PrometheusQueryError
from harness_toolbox.http import HTTPClient, HTTPClientProvider

from perf_harness import PrometheusQuery, PrometheusQueryProbe
from perf_harness.config import load_experiment
from perf_harness.drive.load import LoadPlan, Warmup
from perf_harness.drive.runner import Runner
from perf_harness.engine import Engine, Experiment
from perf_harness.metric.store import MetricStore
from perf_harness.model import Outcome, ResourceProfile, Service, SloAssertion
from perf_harness.observe import ProbeContext
from perf_harness.report import write_run
from perf_harness.runio import load_run


def with_transport(monkeypatch, handler):
    monkeypatch.setattr(
        HTTPClientProvider,
        "create_client",
        lambda provider, _: HTTPClient(
            transport=httpx.MockTransport(handler), timeout=provider.timeout_s, trust_env=False
        ),
    )


def vector(rows, **annotations):
    return {"status": "success", "data": {"resultType": "vector", "result": rows}, **annotations}


async def test_remote_probe_preserves_instance_labels_and_does_not_inherit_load_auth(monkeypatch):
    seen = []

    def handler(request):
        seen.append(request)
        return httpx.Response(
            200,
            json=vector(
                [
                    {"metric": {"__name__": "active", "instance": "pod-a"}, "value": [1, "30"]},
                    {"metric": {"__name__": "active", "instance": "pod-b"}, "value": [1, "50"]},
                ]
            ),
        )

    with_transport(monkeypatch, handler)
    probe = PrometheusQueryProbe(
        url="http://prom",
        service="chat",
        headers={"X-Prom-Token": "metrics"},
        queries=[PrometheusQuery("active", "active", labels=("instance",))],
    )
    async with ClientManager() as clients:
        ctx = ProbeContext(
            Service("chat", base_url="http://chat", headers={"Authorization": "load-secret"}),
            None,
            time.monotonic(),
            clients=clients,
        )
        ctx.sample_time_s = 123.45
        assert await probe.sample(ctx) == {
            'active{instance="pod-a"}': 30,
            'active{instance="pod-b"}': 50,
        }
    assert parse_qs(seen[0].content.decode())["time"] == ["123.45"]
    assert "Authorization" not in seen[0].headers and seen[0].headers["X-Prom-Token"] == "metrics"
    assert probe.describe()[0].name == "prometheus_query.active"


@pytest.mark.parametrize(
    "payload,error",
    [
        (vector([{"metric": {}, "value": [1, "NaN"]}]), ValueError),
        (vector([{"metric": {}, "value": [1, "+Inf"]}]), ValueError),
        (vector([{"metric": {"instance": "a"}, "value": [1, "1"]}]), ValueError),
        (vector([{"metric": {}, "value": [1, "1"]}] * 2), ValueError),
        (vector([], warnings=["partial"]), PrometheusQueryError),
        ({"status": "success", "data": {"resultType": "matrix", "result": []}}, ValueError),
    ],
)
async def test_invalid_or_partial_results_are_not_numeric_observations(monkeypatch, payload, error):
    with_transport(monkeypatch, lambda _: httpx.Response(200, json=payload))
    probe = PrometheusQueryProbe(url="http://prom", queries=[PrometheusQuery("first", "first")])
    async with ClientManager() as clients:
        ctx = ProbeContext(Service("chat"), None, 1, clients=clients)
        with pytest.raises(error):
            await probe.sample(ctx)


def config(probe):
    return (
        "name: remote\nservice: {name: chat, base_url: 'http://chat'}\n"
        "runner: {name: mock}\nresources: [{}]\n"
        "load: {request_rate: 1, max_inflight: 1, hold_s: 1}\n"
        "observe:\n  - name: chat\n    probes:\n      - name: prometheus_query\n" + probe
    )


def test_config_declares_remote_probe_and_slo(tmp_path):
    path = tmp_path / "experiment.yaml"
    path.write_text(
        config(
            "        url: http://prom/proxy\n        timeout_ms: 1000\n        connection_pool_maxsize: 2\n"
            "        queries:\n          - {name: first_p50_s, promql: 'histogram_quantile(0.5, sum by (le) (rate(first_bucket[1m])))', unit: s}\n"
        )
        + "slo: [{metric: 'prometheus_query.first_p50_s{service=\"chat\"}.mean', lt: 10}]\n"
    )
    exp, _ = load_experiment(str(path))
    probe = next(p for p in exp.probes if isinstance(p, PrometheusQueryProbe))
    assert probe.data_source.url == "http://prom/proxy"
    assert probe.data_source.options.connection_pool_maxsize == 2
    assert exp.slo[0].metric == 'prometheus_query.first_p50_s{service="chat"}.mean'


@pytest.mark.parametrize(
    "options,match",
    [
        ("", "explicit `url`"),
        ("        url: http://prom\n        retention_ms: 1000\n", "unknown options"),
        ("        url: http://prom\n        headers: secret\n", "headers must be a mapping"),
    ],
)
def test_config_rejects_ambiguous_remote_sources(tmp_path, options, match):
    path = tmp_path / "experiment.yaml"
    path.write_text(config(options + "        queries: [{name: q, promql: up}]\n"))
    with pytest.raises(ValueError, match=match):
        load_experiment(str(path))


class FastRunner(Runner):
    name = "fast"

    async def fire(self, ctx):
        await asyncio.sleep(0.003)
        return Outcome(status=200, duration_ms=3)


@pytest.mark.parametrize("state", ["data", "empty", "warning"])
async def test_remote_metrics_survive_run_slo_report_and_offline_reload(
    monkeypatch, tmp_path, state
):
    def handler(request):
        assert parse_qs(request.content.decode())["query"] == [
            "sum(rate(first_sum[1m])) / sum(rate(first_count[1m]))"
        ]
        rows = [] if state == "empty" else [{"metric": {}, "value": [time.time(), "2.5"]}]
        extra = {"warnings": ["partial data"]} if state == "warning" else {}
        return httpx.Response(200, json=vector(rows, **extra))

    with_transport(monkeypatch, handler)
    ref = 'prometheus_query.first_mean_s{service="chat"}.mean'
    exp = Experiment(
        name="remote",
        service=Service("chat"),
        runner=FastRunner(),
        resources=[ResourceProfile()],
        loads=[
            LoadPlan(
                request_rate=float("inf"), max_inflight=1, warmup=Warmup(step_s=0), hold_s=0.06
            )
        ],
        probes=[
            PrometheusQueryProbe(
                url="http://prom",
                service="chat",
                queries=[
                    PrometheusQuery(
                        "first_mean_s",
                        "sum(rate(first_sum[1m])) / sum(rate(first_count[1m]))",
                        unit="s",
                        description="Server first output; rolling 1m mean",
                    )
                ],
            )
        ],
        slo=[SloAssertion(metric=ref, op="lt", threshold=3)],
        strict_slo=True,
        observe_interval_s=0.01,
    )
    run = await Engine(exp, run_id=state).run()
    assert run.passed is (state == "data")
    assert bool(run.arm_runs[0].probe_errors) is (state == "warning")
    write_run(run, str(tmp_path))
    path = tmp_path / "remote" / state
    restored = load_run(path)
    arm = restored.arm_runs[0]
    assert (path / "report.html").exists()
    if state == "data":
        assert MetricStore(restored.arm_runs).query(arm, ref) == 2.5
        assert "Server first output" in arm.metrics["prometheus_query.first_mean_s"].description
        assert "prometheus_query.first_mean_s" in (path / "timeseries.csv").read_text()
        assert arm.slo[0].state == "pass"
    else:
        assert arm.slo[0].state == "skipped"
