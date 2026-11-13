"""HTTP matching, window queries and graph reporting use one offline evidence path."""

import json
from dataclasses import replace
from pathlib import Path
from urllib.parse import parse_qs

import httpx
import pytest
import yaml
from harness_common import Graph, HttpOperation, Node, NodeKind, Relation, RelationKind
from harness_toolbox.http import HTTPClient, HTTPClientProvider
from harness_toolbox.prometheus import PrometheusDataSource

from perf_harness import Engine, build_callgraph, load_experiment, load_run, write_run
from perf_harness.metric.store import MetricStore
from perf_harness.observe.http_api import HttpApiProbe, HttpMetricProfile
from perf_harness.report.callgraph import section


def profile(**kwargs):
    return HttpMetricProfile(
        requests_metric="http_requests_total", duration_bucket="http_duration_bucket", **kwargs
    )


def test_http_matching_scopes_service_method_and_route_and_escapes_values():
    operation = HttpOperation(
        "retrieve", method="POST", path="/faqs/{dataset_id}", service_name="kb"
    )
    metrics = profile(
        service_label="job",
        route_label="path",
        method_label="method",
        service_value="kb-metrics",
        match={"namespace": 'qa"\\cluster'},
        duration_unit="ms",
    )
    probe = HttpApiProbe(
        operation=operation,
        profile=metrics,
        data_source=PrometheusDataSource("http://prom"),
        metric_route="/faqs/:dataset_id",
    )
    selector = metrics.selector(operation, "/faqs/:dataset_id")
    assert 'job="kb-metrics"' in selector and 'method="POST"' in selector
    assert 'path="/faqs/:dataset_id"' in selector
    assert json.dumps('qa"\\cluster') in selector
    p95 = next(q.promql for q in probe.summaries if q.name == "p95")
    assert "histogram_quantile(0.95, sum by (le) (rate(" in p95
    assert p95.endswith(" / 1000") and "$window" in p95
    assert probe.operation_metrics.operation == operation.id()
    assert probe.operation_metrics.path == "/faqs/{dataset_id}"
    assert metrics.selector(replace(operation, method="GET")) != metrics.selector(operation)
    assert profile().selector(replace(operation, service_name="other")) != profile().selector(
        operation
    )


@pytest.mark.parametrize(
    "options",
    [
        {"route_label": "bad-label"},
        {"match": {"http_route": "/override"}},
        {"match": {"namespace": 1}},
        {"duration_unit": "minutes"},
        {"qps_semantics": "unknown"},
        {"rate_interval": "0s"},
        {"method_label": "http_route"},
    ],
)
def test_bad_profile_fails_before_network_access(options):
    with pytest.raises(ValueError):
        profile(**options)


def configuration(tmp_path):
    graph = Graph(
        environment="qa",
        nodes=(
            Node("service:entry", NodeKind.SERVICE),
            Node("service:kb", NodeKind.SERVICE),
            Node("operation:entry/ask", NodeKind.OPERATION),
            Node("operation:kb/retrieve", NodeKind.OPERATION, properties={"team": "search"}),
        ),
        relations=(
            Relation("operation:entry/ask", "service:entry", RelationKind.BELONGS_TO),
            Relation("operation:kb/retrieve", "service:kb", RelationKind.BELONGS_TO),
            Relation("operation:entry/ask", "operation:kb/retrieve", RelationKind.MAY_CALL),
        ),
    )
    (tmp_path / "case.graph.json").write_text(json.dumps(graph.to_dict()))
    raw = {
        "graph": "case.graph.json",
        "prometheus": {"url": "http://prom", "step_s": 0.001},
        "service": {"name": "entry", "base_url": "http://unused", "environment": {"name": "qa"}},
        "runner": {"name": "mock"},
        "cases": [{"id": "faq", "input": {"ms": 1}}],
        "load": {"request_rate": "inf", "max_inflight": 1, "hold_s": 0.02, "warmup": {"step_s": 0}},
        "observe": [
            {
                "name": "kb",
                "http_metrics": {
                    "requests_metric": "http_requests_total",
                    "duration_bucket": "http_duration_bucket",
                    "match": {"namespace": "qa"},
                },
                "probes": [
                    {
                        "name": "http_api",
                        "operation": {
                            "name": "retrieve",
                            "method": "POST",
                            "path": "/faqs/{dataset_id}",
                        },
                    }
                ],
            }
        ],
    }
    path = tmp_path / "run.yaml"
    path.write_text(yaml.safe_dump(raw))
    return path


@pytest.mark.parametrize("state", ["traffic", "zero", "missing", "error"])
async def test_http_query_to_saved_graph_is_offline_and_preserves_missing(
    monkeypatch, tmp_path, state
):
    experiment, _ = load_experiment(str(configuration(tmp_path)))

    def handler(request):
        params = parse_qs(request.content.decode())
        assert 'service_name="kb"' in params["query"][0]
        if state == "error":
            return httpx.Response(503)
        p95 = "histogram_quantile" in params["query"][0]
        value = "2.5" if p95 else ("0" if state == "zero" else "4")
        is_range = request.url.path.endswith("query_range")
        timestamp = float(params["start" if is_range else "time"][0])
        row = {
            "metric": {},
            "values" if is_range else "value": [[timestamp, value]]
            if is_range
            else [timestamp, value],
        }
        return httpx.Response(
            200,
            json={
                "status": "success",
                "data": {
                    "resultType": "matrix" if is_range else "vector",
                    "result": [] if state == "missing" else [row],
                },
            },
        )

    monkeypatch.setattr(
        HTTPClientProvider,
        "create_client",
        lambda *_: HTTPClient(transport=httpx.MockTransport(handler)),
    )
    run = await Engine(experiment, run_id=state).run()
    assert run.status == "completed"
    paths = write_run(run, str(tmp_path / "runs"))
    (tmp_path / "case.graph.json").unlink()
    restored = load_run(paths["run_dir"])
    assert restored.operation_metrics == run.operation_metrics and restored.case_ids == ["faq"]
    assert all("$window" not in q.expression for q in restored.arm_runs[0].prometheus_observations)
    if state in {"traffic", "zero"}:
        assert (
            MetricStore(restored.arm_runs).query(
                restored.arm_runs[0], restored.operation_metrics[0].p95
            )
            == 2.5
        )
        assert not restored.arm_runs[0].probe_errors

    def offline(*_):
        raise AssertionError("report must not access network")

    monkeypatch.setattr(HTTPClientProvider, "create_client", offline)
    snapshot = build_callgraph(restored)
    payload = json.loads(Path(paths["callgraph"]).read_text())
    metric = payload["arms"][0]["windows"][0]["nodes"][1]["properties"]["perf"]["metrics"]["qps"]
    assert metric["value"] == {"traffic": 4, "zero": 0, "missing": None, "error": None}[state]
    assert (
        metric["status"]
        == {"traffic": "observed", "zero": "observed", "missing": "missing", "error": "error"}[
            state
        ]
    )
    props = payload["arms"][0]["windows"][0]["nodes"][1]["properties"]
    if state == "missing":
        assert metric["reason"] == "no_samples"
    assert props["team"] == "search"
    assert restored.graph.node("operation:kb/retrieve").properties == {"team": "search"}
    assert payload["topology_source"] == "declared"
    assert payload["relations"][0]["kind"] == "may_call"
    assert payload["arms"][0]["load"]["request_rate"] == "inf"
    assert snapshot.to_dict() == build_callgraph(restored).to_dict()
    view = section(snapshot).blocks[-1]
    frame = view.frames[0]
    assert len(frame.nodes) == 2 and len(frame.edges) == 1
    assert frame.edges[0].dashed
    node = next(n for n in frame.nodes if n.id == "operation:kb/retrieve")
    expected = {"traffic": "QPS 4", "zero": "QPS 0", "missing": "QPS N/A", "error": "QPS N/A"}[
        state
    ]
    assert expected in node.label
    assert "QPS N/A" in frame.nodes[0].label  # Unbound entry operation is not zero.
    if state in {"error", "missing"}:
        assert any(key == "Query error" for key, _ in node.details)
    from perf_harness.cli import main

    assert main(["report", paths["run_dir"]]) == 0
    html = Path(paths["report_html"]).read_text()
    assert "Call Graph" in html and "cytoscape" in html
    assert 'aria-selected="true" tabindex="0">Report</button>' in html
    assert 'aria-selected="false" tabindex="-1">Callgraph</button>' in html
    assert html.index("执行状态</summary>") < html.index('id="rk-panel-1"')
    assert html.index('id="rk-panel-1"') < html.index("Call Graph</summary>")
    assert "<script src=" not in html
    assert "API 观测（measurement）</summary>" not in html
    assert '<details class="graph-table"><summary>指标明细</summary>' in html
    from perf_harness.report.render import _api_observations

    tables = {
        (arm.id, window.id): _api_observations([arm], window.id)
        for arm in restored.arm_runs
        for window in arm.windows
    }
    frames = section(snapshot, tables).blocks[-1].frames
    for arm in snapshot.arms:
        for window in arm.windows:
            frame = next(
                f for f in frames if f.label == f"{arm.arm_id} / {window.id} ({window.kind})"
            )
            assert frame.table is tables[arm.id, window.id]
            assert all(row[0] == arm.id and row[1] == window.id for row in frame.table.rows)


@pytest.mark.parametrize("roles", [("qps",), ("p95",), ("qps", "p95")])
def test_operation_roles_are_independent_and_preserve_meaning(tmp_path, roles):
    from harness_common import Operation

    from perf_harness import OperationMetricBinding, Run
    from perf_harness.drive.load import LoadPlan
    from perf_harness.metric import ScalarSummary
    from perf_harness.model import (
        Arm,
        ArmRun,
        RequestStats,
        ResourceProfile,
        Window,
    )
    from perf_harness.observe import PrometheusQueryProbe, WindowQuery

    operation = Operation("search", service_name="search-store")
    meanings = {
        "qps": "Completed requests from shared counter",
        "p95": "Logged search took; excludes network",
    }
    expressions = {
        "qps": "sum(rate(search_requests_total[$window]))",
        "p95": "histogram_quantile(0.95, sum by (le) (increase(log_duration_bucket[$window])))",
    }
    probe = PrometheusQueryProbe(
        data_source=PrometheusDataSource("http://prom"),
        operation=operation,
        service="search-store",
        summaries=[
            WindowQuery(
                role,
                expressions[role],
                unit={"qps": "rps", "p95": "s"}[role],
                description=meanings[role],
            )
            for role in roles
        ],
        operation_metrics={role: role for role in roles},
    )
    binding = probe.operation_metrics
    assert isinstance(binding, OperationMetricBinding)
    assert binding.method == binding.path == ""
    assert binding.descriptions == {role: meanings[role] for role in roles}
    graph = Graph(
        environment="qa",
        nodes=(
            Node("service:search-store", NodeKind.SERVICE),
            Node(operation.id(), NodeKind.OPERATION),
        ),
        relations=(Relation(operation.id(), "service:search-store", RelationKind.BELONGS_TO),),
    )
    values = {"qps": 3.5, "p95": 0.04}
    arm = ArmRun(
        id="arm",
        service="search-store",
        arm=Arm("arm", ResourceProfile(), LoadPlan(request_rate=1, max_inflight=1)),
        series={},
        windows=[
            Window(
                "measurement",
                "measurement",
                "measurement",
                0,
                10,
                True,
                request=RequestStats(0, 0, 0, 0, 0, 0, 0, {}),
                probe_metrics={
                    {"qps": binding.qps, "p95": binding.p95}[role].removesuffix(
                        ".value"
                    ): ScalarSummary(values[role])
                    for role in roles
                },
            )
        ],
        metrics={family.name: family for family in probe.describe()},
    )
    run = Run(
        run_id="middleware",
        experiment="test",
        service="search-store",
        created_at="2026-11-13T00:00:00Z",
        environment="qa",
        executions=[arm],
        graph=graph,
        operation_metrics=[binding],
    )
    paths = write_run(run, str(tmp_path))
    restored = load_run(paths["run_dir"])
    assert restored.operation_metrics == [binding]
    snapshot = build_callgraph(restored)
    props = snapshot.arms[0].windows[0].nodes[0].to_dict()["properties"]["perf"]
    for role in ("qps", "p95"):
        metric = props["metrics"][role]
        if role in roles:
            assert metric["value"] == values[role]
            assert metric["description"] == meanings[role]
            details = section(snapshot).blocks[-1].frames[0].nodes[0].details
            assert (role + " description", meanings[role]) in details
        else:
            assert metric["value"] is None and metric["reason"] == "unbound"
    if "qps" not in roles:
        assert props["qps_semantics"] is None


def test_operation_role_descriptions_cannot_change_between_windows():
    from harness_common import Operation

    from perf_harness.model import WindowSelector
    from perf_harness.observe import PrometheusQueryProbe, WindowQuery

    with pytest.raises(ValueError, match="descriptions must agree"):
        PrometheusQueryProbe(
            data_source=PrometheusDataSource("http://prom"),
            operation=Operation("search", service_name="search-store"),
            service="search-store",
            operation_metrics={"p95": "latency"},
            summaries=[
                WindowQuery(
                    "latency",
                    "p95",
                    unit="s",
                    description="all requests",
                    window=WindowSelector(kind="measurement"),
                ),
                WindowQuery(
                    "latency",
                    "p95",
                    unit="s",
                    description="success only",
                    window=WindowSelector(kind="observation"),
                ),
            ],
        )


@pytest.mark.parametrize("mode", ["unary", "sse"])
def test_http_response_mode_survives_configuration_binding_and_request_roundtrip(tmp_path, mode):
    from harness_common import OperationRun, SSEOperation

    from perf_harness.model import Outcome, Service
    from perf_harness.runio import _operation_from, _operation_json

    path = configuration(tmp_path)
    raw = yaml.safe_load(path.read_text())
    raw["observe"][0]["probes"][0]["operation"]["response_mode"] = mode
    path.write_text(yaml.safe_dump(raw))
    experiment, _ = load_experiment(str(path))
    probe = next(p for p in experiment.probes if isinstance(p, HttpApiProbe))
    assert probe.operation_metrics.response_mode == mode
    operation_type = SSEOperation if mode == "sse" else HttpOperation
    call = OperationRun(
        id="call-1",
        service=Service("kb"),
        operation=operation_type(
            "retrieve", service_name="kb", method="POST", path="/faqs/{dataset_id}"
        ),
        outcome=Outcome(status=200, duration_ms=50),
    )
    serialized = _operation_json(call)
    restored = _operation_from(serialized)
    assert type(restored.operation) is operation_type
    assert restored.operation == call.operation
    assert _operation_json(restored) == serialized
    serialized["operation"]["response_mode"] = "unknown"
    with pytest.raises(ValueError, match="response mode"):
        _operation_from(serialized)
