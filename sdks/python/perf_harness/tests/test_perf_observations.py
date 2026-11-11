"""Request results partition completion events; metrics do not imply acceptance."""

import json
from pathlib import Path

import pytest
from harness_common import Operation, OperationRun

from perf_harness import (
    Arm,
    ArmRun,
    Engine,
    Environment,
    Experiment,
    LoadPlan,
    MockRunner,
    Outcome,
    RequestEvaluation,
    ResourceProfile,
    Run,
    Service,
    Warmup,
    Window,
)
from perf_harness.config import load_experiment
from perf_harness.judge import default_judge
from perf_harness.metric import Missing
from perf_harness.metric.reduce import reduce_requests
from perf_harness.metric.store import MetricStore
from perf_harness.observe import MetricProbe, PrometheusQuery
from perf_harness.records import RequestRecord
from perf_harness.report import write_run
from perf_harness.runio import load_run, write_run_data
from perf_harness.verdict import build_verdict_doc

FIXTURE = json.loads(
    (Path(__file__).parents[4] / "conformance/perf/fixtures/throughput.json").read_text()
)


def test_result_partition_uses_completion_windows_and_preserves_evidence(tmp_path):
    service = Service("chat", environment=Environment("qa"))
    operation = Operation("ask", service_name=service.name)
    arm = ArmRun(
        id="run:one",
        service=service.name,
        arm=Arm("one", ResourceProfile(), LoadPlan(request_rate=8, max_inflight=20)),
        windows=[],
        series={},
    )
    for item in FIXTURE["requests"]:
        state = item.get("state", "finished")
        record = RequestRecord(
            item["id"], "case", item["start"], item["start"], state=state, facets={"kind": "chat"}
        )
        if state != "dropped":
            record.dispatched_at = item["start"]
            record.finished_at = item["end"]
            record.operation_run_id = item["id"]
            arm.operation_runs.append(
                OperationRun(
                    item["id"],
                    service,
                    operation,
                    Outcome(status=200, duration_ms=(item["end"] - item["start"]) * 1000),
                )
            )
        if item.get("result"):
            arm.evaluations[item["id"]] = RequestEvaluation(item["result"])
        arm.requests.append(record)
    for window in FIXTURE["windows"]:
        stat = reduce_requests(arm, window["start"], window["end"])
        assert {k: getattr(stat, k) for k in window["counts"]} == window["counts"]
        assert stat.throughput_rps == pytest.approx(
            sum(
                [
                    stat.success_rps,
                    stat.failure_rps,
                    stat.rejection_rps,
                    stat.timeout_rps,
                    stat.unjudged_rps,
                ]
            )
        )
        assert reduce_requests(arm, window["start"], window["end"], facet=("kind", "chat")) == stat
    arm.windows = [
        Window(
            "measurement",
            "measurement",
            "measurement",
            0,
            10,
            True,
            request=reduce_requests(arm, 0, 10),
        )
    ]
    assert arm.windows[0].request.n_judged == 4  # late success belongs to dispatch cohort
    assert arm.windows[0].request.error_rate == 3 / 4
    run = Run(
        run_id="run",
        experiment="facts",
        created_at="now",
        service="chat",
        environment="qa",
        executions=[arm],
    )
    write_run_data(run, tmp_path)
    raw = (tmp_path / "requests.jsonl").read_bytes()
    restored = load_run(tmp_path)
    assert restored.environment == "qa"
    assert restored.arm_runs[0].measurement.request == arm.measurement.request
    restored.arm_runs[0].evaluations.clear()
    stats = reduce_requests(restored.arm_runs[0], 0, 10)
    assert stats.unjudged == stats.completed == 5 and stats.error_breakdown == {}
    restored.arm_runs[0].measurement.request = stats
    assert isinstance(
        MetricStore(restored.arm_runs).query(restored.arm_runs[0], "error_rate"), Missing
    )
    write_run_data(restored, tmp_path)
    assert (tmp_path / "requests.jsonl").read_bytes() == raw


@pytest.mark.parametrize(
    "status,exc,result",
    [
        (200, None, "success"),
        (429, None, "rejected"),
        (503, None, "failure"),
        (504, None, "timeout"),
        (None, "ReadTimeout", "timeout"),
    ],
)
def test_default_result_classification(status, exc, result):
    assert default_judge(Outcome(status=status, duration_ms=1, meta={"exc": exc})).result == result


@pytest.mark.parametrize("key", ["slo", "strict_slo", "abort_on_fail"])
def test_removed_acceptance_configuration_is_rejected(tmp_path, key):
    path = tmp_path / "perf.yaml"
    path.write_text(
        f"service: {{name: chat, base_url: http://chat}}\nrunner: {{name: mock}}\n"
        f"load: {{request_rate: 8, max_inflight: 20}}\n{key}: []\n"
    )
    with pytest.raises(ValueError, match="Removed acceptance"):
        load_experiment(str(path))


async def test_downstream_operations_remain_distinct_offline_without_graph(tmp_path, monkeypatch):
    path = tmp_path / "perf.yaml"
    path.write_text("""name: api-observations
service: {name: chat, base_url: http://chat, environment: {name: qa}}
runner: {name: mock, base_ms: 1}
load: {request_rate: 20, max_inflight: 2, warmup: {step_s: 0}, hold_s: 0.06}
observe_interval_s: 0.01
observe:
- name: search
  base_url: http://search
  probes:
  - name: metric
    operation: search
    queries: [{name: latency_ms, promql: search_latency, unit: ms}]
  - name: metric
    operation: index
    queries: [{name: latency_ms, promql: index_latency, unit: ms}]
""")

    async def sample(self, ctx):
        return {"latency_ms": 10 if self.operation.name == "search" else 20}

    monkeypatch.setattr(MetricProbe, "sample", sample)
    exp, _ = load_experiment(str(path))
    run = await Engine(exp, run_id="facts").run()
    write_run(run, str(tmp_path))
    directory = tmp_path / "api-observations" / "facts"
    restored = load_run(directory)
    arm = restored.arm_runs[0]
    store = MetricStore(restored.arm_runs)
    for name, expected in [("search", 10), ("index", 20)]:
        op = Operation(name, service_name="search")
        ref = f'metric.latency_ms{{service="search",operation="{op.id()}"}}.mean'
        assert store.query(arm, ref) == expected
        assert op.id() in (directory / "timeseries.csv").read_text()
        assert op.id() in (directory / "report.md").read_text()
    assert run.status == "completed" and build_verdict_doc(run)["status"] == "skipped"
    assert "slo" not in json.loads((directory / "run.json").read_text())["executions"][0]


def test_operation_binding_rejects_wrong_owner_and_query_override():
    with pytest.raises(ValueError, match="owner"):
        MetricProbe(
            service="a",
            operation=Operation("ask", service_name="b"),
            queries=[PrometheusQuery("latency", "x")],
        )
    with pytest.raises(ValueError, match="override"):
        MetricProbe(
            service="a",
            operation=Operation("ask", service_name="a"),
            queries=[PrometheusQuery("latency", "x", labels=("operation",))],
        )


async def test_request_failures_do_not_fail_an_experiment():
    exp = Experiment(
        service=Service("chat"),
        runner=MockRunner(base_ms=1),
        resources=[ResourceProfile()],
        loads=[LoadPlan(request_rate=100, max_inflight=2, hold_s=0.03, warmup=Warmup(0))],
        judge=lambda o: RequestEvaluation("failure", "business"),
    )
    run = await Engine(exp).run()
    assert run.status == "completed"
    assert run.arm_runs[0].measurement.request.failure_rps > 0
    assert build_verdict_doc(run)["status"] == "skipped"
