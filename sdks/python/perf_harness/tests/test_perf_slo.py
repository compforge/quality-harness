import pytest

from perf_harness.cli import main
from perf_harness.config import load_experiment
from perf_harness.drive.load import LoadPlan, Stage
from perf_harness.drive.runner import MockRunner
from perf_harness.engine import Engine, Experiment
from perf_harness.metric import GaugeSummary, MetricFamily
from perf_harness.model import (
    Arm,
    ArmRun,
    ArmStop,
    RequestStats,
    ResourceProfile,
    Service,
    SloAssertion,
    Window,
    WindowSelector,
)
from perf_harness.slo import evaluate_slo, slo_aware_capacity


def _arm_run(level: float, p99: float, *, err: float = 0.0, n_dropped: int = 0) -> ArmRun:
    stats = RequestStats(
        n=100,
        n_ok=int(round(100 * (1 - err))),
        throughput_rps=level,
        p50_ms=p99 / 2,
        p95_ms=p99,
        p99_ms=p99,
        error_rate=err,
        error_breakdown={},
        n_dropped=n_dropped,
    )
    resources = ResourceProfile(workers=2)
    load = LoadPlan(request_rate=level, max_concurrency=128, duration_s=(0.0 + 1.0))
    return ArmRun(
        id=f"{resources.label()}|{load.label()}",
        service="s",
        arm=Arm(f"{resources.label()}|{load.label()}", resources, load),
        windows=[
            Window("measurement", "measurement", "measurement", 0.0, 1.0, True, request=stats),
            Window("stage-0", f"hold@{level:g}", "hold", 0.0, 1.0, True, level, stats),
        ],
        series={},
    )


# ---- evaluate_slo (pure) ----


def test_slo_pass_and_fail():
    t = _arm_run(40, p99=100)
    assert evaluate_slo(t, [SloAssertion("p99_ms", "lt", 200)])[0].passed
    c = evaluate_slo(t, [SloAssertion("p99_ms", "lt", 50)])[0]
    assert not c.passed and c.observed == 100


def test_slo_between_and_gte():
    t = _arm_run(40, p99=100)
    assert evaluate_slo(t, [SloAssertion("throughput_rps", "gte", 40)])[0].passed
    assert evaluate_slo(t, [SloAssertion("p99_ms", "between", (50, 150))])[0].passed
    assert not evaluate_slo(t, [SloAssertion("p99_ms", "between", (0, 50))])[0].passed


def test_slo_window_level_selects_matching_hold():
    a = SloAssertion("p99_ms", "lt", 2000, window=WindowSelector(kind="hold", level=40))
    assert evaluate_slo(_arm_run(10, 100), [a])[0].skipped
    assert not evaluate_slo(_arm_run(40, 5000), [a])[0].passed


def test_slo_missing_label_slice_is_skipped():
    # a facet label whose value no run produced → SKIPPED (three-state): not a failure,
    # but not a pass either — a skip never counts as green
    c = evaluate_slo(_arm_run(40, 100), [SloAssertion('p99_ms{difficulty="x"}', "lt", 1)])[0]
    assert c.observed is None and c.skipped and not c.passed


def test_cooldown_slo_reads_exact_resource_series_labels():
    t = _arm_run(40, p99=100)
    sid = 'metrics.task_count{service="worker",state="running",task_type="batch"}'
    t.metrics = {
        "metrics.task_count": MetricFamily(
            "metrics.task_count", "count", "resource", "gauge", "http"
        )
    }
    t.windows.append(
        Window(
            "cooldown",
            "cooldown",
            "cooldown",
            1.0,
            1.2,
            True,
            probe_metrics={sid: GaugeSummary(last=0.0, mean=1.5, peak=3.0)},
        )
    )
    ref = f"{sid}.last"
    check = evaluate_slo(
        t,
        [SloAssertion(ref, "lte", 0, window=WindowSelector(kind="cooldown"))],
    )[0]
    assert check.passed and check.observed == 0.0


def test_slo_facet_label_reads_the_facet_slice():
    t = _arm_run(40, p99=100)  # overall p99 = 100
    simple = RequestStats(
        n=10,
        n_ok=10,
        throughput_rps=5,
        p50_ms=10,
        p95_ms=20,
        p99_ms=30,
        error_rate=0.0,
        error_breakdown={},
    )
    t.measurement.by_facet = {"difficulty": {"simple": simple}}
    # the facet label selects the `simple` slice → p99 there is 30, not the overall 100
    c = evaluate_slo(t, [SloAssertion('p99_ms{difficulty="simple"}', "lt", 50)])[0]
    assert c.observed == 30 and c.passed
    # while overall (no label) sees 100 → fails the same budget
    assert not evaluate_slo(t, [SloAssertion("p99_ms", "lt", 50)])[0].passed


def test_slo_aware_capacity_is_highest_passing_level():
    a = SloAssertion("p99_ms", "lt", 2000, window=WindowSelector(kind="hold"))
    t10, t20, t40 = _arm_run(10, 100), _arm_run(20, 100), _arm_run(40, 5000)
    t10.slo = evaluate_slo(t10, [a])
    t20.slo = evaluate_slo(t20, [a])
    t20.stop = ArmStop(reason="error_rate")
    next(window for window in t20.windows if window.kind == "hold").complete = False
    t40.slo = evaluate_slo(t40, [a])
    # 20 passes its SLO on the partial sample, but only the complete 10-level arm_run
    # confirms capacity.
    assert slo_aware_capacity([t10, t20, t40]) == {"w2|rate": 10}


def test_slo_aware_capacity_uses_passing_holds_in_multi_stage_arm_run():
    t = _arm_run(40, 5000)
    load = LoadPlan(
        request_rate=10,
        max_concurrency=128,
        duration_s=2,
        stages=(Stage(1, 10, 128), Stage(1, 40, 128)),
    )
    t.arm = Arm(t.arm.id, t.arm.resources, load)
    t.windows = [
        t.measurement,
        Window(
            "stage-0", "hold@10", "hold", 0.0, 1.0, True, 10, _arm_run(10, 100).measurement.request
        ),
        Window(
            "stage-1", "hold@40", "hold", 1.0, 2.0, True, 40, _arm_run(40, 5000).measurement.request
        ),
    ]
    t.slo = evaluate_slo(
        t, [SloAssertion("p99_ms", "lt", 2000, window=WindowSelector(kind="hold"))]
    )

    assert slo_aware_capacity([t]) == {"w2|rate": 10}


def test_slo_aware_capacity_does_not_treat_multi_stage_peak_as_capacity():
    t = _arm_run(40, 100)
    load = LoadPlan(
        request_rate=10,
        max_concurrency=128,
        duration_s=2,
        stages=(Stage(1, 10, 128), Stage(1, 40, 128)),
    )
    t.arm = Arm(t.arm.id, t.arm.resources, load)
    t.slo = evaluate_slo(t, [SloAssertion("p99_ms", "lt", 2000)])

    assert slo_aware_capacity([t]) == {"w2|rate": None}


def test_slo_aware_capacity_applies_global_resource_slo_to_each_hold():
    t = _arm_run(40, 100)
    load = LoadPlan(
        request_rate=10,
        max_concurrency=128,
        duration_s=2,
        stages=(Stage(1, 10, 128), Stage(1, 40, 128)),
    )
    t.arm = Arm(t.arm.id, t.arm.resources, load)
    t.windows = [
        t.measurement,
        Window("stage-0", "hold@10", "hold", 0.0, 1.0, True, 10, t.measurement.request),
        Window("stage-1", "hold@40", "hold", 1.0, 2.0, True, 40, t.measurement.request),
    ]
    t.metrics = {"top.cpu_m": MetricFamily("top.cpu_m", "m", "resource", "gauge", "k8s")}
    for window in t.windows[1:]:
        window.probe_metrics['top.cpu_m{service="worker"}'] = GaugeSummary(
            last=1200, mean=1200, peak=1200
        )
    t.slo = evaluate_slo(
        t,
        [
            SloAssertion("error_rate", "lt", 0.1, window=WindowSelector(kind="hold")),
            SloAssertion(
                'top.cpu_m{service="worker"}.peak',
                "lt",
                1000,
                window=WindowSelector(kind="hold"),
            ),
        ],
    )

    assert slo_aware_capacity([t]) == {"w2|rate": None}


# ---- config parse + fail-fast ----

_BASE = """
service: { name: s, base_url: "http://x" }
resources: [ {} ]
runner: { name: mock }
"""


def _write(tmp_path, extra: str) -> str:
    cfg = tmp_path / "c.yaml"
    cfg.write_text(_BASE + extra)
    return str(cfg)


def test_parse_slo_and_abort(tmp_path):
    extra = (
        "abort_on_fail: true\n"
        "slo:\n"
        "  - { metric: p99_ms, lt: 2000 }\n"
        "  - { metric: error_rate, lt: 0.01, window: {kind: hold, level: 40} }\n"
        "load: { request_rate: 40, max_concurrency: 128, duration_s: 0.1 }\n"
    )
    exp, _ = load_experiment(_write(tmp_path, extra))
    assert exp.abort_on_fail
    assert len(exp.slo) == 2
    assert exp.slo[0].op == "lt" and exp.slo[0].threshold == 2000
    assert exp.slo[1].window.level == 40


def test_parse_cooldown_slo(tmp_path):
    extra = (
        "cooldown_s: 1\n"
        "slo:\n"
        "  - { metric: client.inflight.last, window: {kind: cooldown}, lte: 0 }\n"
        "load: { request_rate: 1, max_concurrency: 128, duration_s: 0.1 }\n"
    )
    exp, _ = load_experiment(_write(tmp_path, extra))
    assert exp.slo[0].window.kind == "cooldown"


def test_cooldown_slo_accepts_resource_series_labels(tmp_path):
    extra = (
        "cooldown_s: 1\n"
        "observe:\n"
        "  - name: worker\n"
        "    probes:\n"
        "      - name: prometheus\n"
        "        url: http://worker/metrics\n"
        "        queries:\n"
        "          - { name: task_count, promql: 'sum by (task_type, state) (task_count)', "
        "kind: gauge, labels: [task_type, state] }\n"
        "slo:\n"
        '  - { metric: \'prometheus.task_count{service="worker",'
        'task_type="batch",state="running"}.last\', window: {kind: cooldown}, lte: 0 }\n'
        "load: { request_rate: 1, max_concurrency: 128, duration_s: 0.1 }\n"
    )
    exp, _ = load_experiment(_write(tmp_path, extra))
    assert exp.slo[0].window.kind == "cooldown"


def test_cooldown_slo_rejects_unknown_resource_label(tmp_path):
    extra = (
        "cooldown_s: 1\n"
        "observe:\n"
        "  - name: worker\n"
        "    probes:\n"
        "      - name: prometheus\n"
        "        url: http://worker/metrics\n"
        "        queries:\n"
        "          - { name: task_count, promql: 'sum by (task_type, state) (task_count)', "
        "kind: gauge, labels: [task_type, state] }\n"
        "slo:\n"
        '  - { metric: \'prometheus.task_count{service="worker",'
        'task_tipe="batch",state="running"}.last\', window: {kind: cooldown}, lte: 0 }\n'
        "load: { request_rate: 1, max_concurrency: 128, duration_s: 0.1 }\n"
    )
    with pytest.raises(ValueError, match="unknown labels.*task_tipe"):
        load_experiment(_write(tmp_path, extra))


def test_cooldown_slo_skips_stale_or_failed_probe_data():
    t = _arm_run(40, p99=100)
    sid = 'metrics.task_count{service="worker",state="running",task_type="batch"}'
    t.metrics = {
        "metrics.task_count": MetricFamily(
            "metrics.task_count",
            "count",
            "resource",
            "gauge",
            "http",
            labels=frozenset({"service", "state", "task_type"}),
        )
    }
    t.windows.append(Window("cooldown", "cooldown", "cooldown", 1.0, 1.2, True))
    assertion = SloAssertion(f"{sid}.last", "lte", 0, window=WindowSelector(kind="cooldown"))
    assert evaluate_slo(t, [assertion])[0].skipped


def test_request_slo_unknown_facet_key_still_fails_fast(tmp_path):
    extra = (
        "slo: [ { metric: 'p99_ms{unknown=\"x\"}', lte: 100 } ]\n"
        "load: { request_rate: 1, max_concurrency: 128, duration_s: 0.1 }\n"
    )
    with pytest.raises(ValueError, match="facet unknown=x unknown"):
        load_experiment(_write(tmp_path, extra))


def test_cooldown_slo_requires_cooldown(tmp_path):
    extra = (
        "slo: [ { metric: client.inflight.last, window: {kind: cooldown}, lte: 0 } ]\n"
        "load: { request_rate: 1, max_concurrency: 128, duration_s: 0.1 }\n"
    )
    with pytest.raises(ValueError, match="requires cooldown_s"):
        load_experiment(_write(tmp_path, extra))


def test_cooldown_slo_rejects_request_metric(tmp_path):
    extra = (
        "cooldown_s: 1\n"
        "slo: [ { metric: p99_ms, window: {kind: cooldown}, lte: 100 } ]\n"
        "load: { request_rate: 1, max_concurrency: 128, duration_s: 0.1 }\n"
    )
    with pytest.raises(ValueError, match="resource-side time-sampled"):
        load_experiment(_write(tmp_path, extra))


def test_bad_slo_window_fails_fast(tmp_path):
    extra = (
        "slo: [ { metric: p99_ms, window: {kind: recovery}, lte: 100 } ]\n"
        "load: { request_rate: 1, max_concurrency: 128, duration_s: 0.1 }\n"
    )
    with pytest.raises(ValueError, match="slo.window"):
        load_experiment(_write(tmp_path, extra))


def test_bad_slo_metric_fails_fast(tmp_path):
    extra = "slo: [ { metric: latency, lt: 1 } ]\nload: { request_rate: 1, max_concurrency: 128, duration_s: 0.1 }\n"
    with pytest.raises(ValueError, match="slo.metric"):
        load_experiment(_write(tmp_path, extra))


def test_slo_needs_exactly_one_op(tmp_path):
    extra = "slo: [ { metric: p99_ms } ]\nload: { request_rate: 1, max_concurrency: 128, duration_s: 0.1 }\n"
    with pytest.raises(ValueError, match="exactly one"):
        load_experiment(_write(tmp_path, extra))


# ---- engine integration ----


def _exp(slo, *, abort=False) -> Experiment:
    return Experiment(
        service=Service("mock", base_url="http://127.0.0.1:0"),
        runner=MockRunner(base_ms=2),
        resources=[ResourceProfile()],
        loads=[
            LoadPlan(request_rate=float("inf"), max_concurrency=lv, duration_s=(0.0 + 0.15))
            for lv in (2, 4)
        ],
        slo=slo,
        abort_on_fail=abort,
    )


async def test_engine_run_passes_and_fails_on_slo():
    ok = await Engine(_exp([SloAssertion("p99_ms", "lt", 100000)])).run()
    assert ok.passed and len(ok.arm_runs) == 2
    assert all(all(check.passed for check in arm_run.slo) for arm_run in ok.arm_runs)

    bad = await Engine(_exp([SloAssertion("p99_ms", "lt", 1)])).run()  # 1ms is unmeetable
    assert not bad.passed


async def test_engine_abort_on_fail_stops_sweep():
    run = await Engine(_exp([SloAssertion("p99_ms", "lt", 1)], abort=True)).run()
    assert not run.passed
    assert len(run.arm_runs) == 1  # stopped after the first failing arm_run


# ---- CLI exit code (CI gate) ----


def test_cli_exit_code_reflects_slo(tmp_path):
    cfg = _write(
        tmp_path,
        "slo: [ { metric: p99_ms, lt: 1 } ]\nload: { request_rate: inf, max_concurrency: 2, duration_s: 0.1 }\n",
    )
    assert main(["run", cfg, "--out", str(tmp_path / "runs"), "--mock"]) == 1


def test_capacity_never_compares_rate_and_concurrency_units():
    from dataclasses import replace

    rate, concurrency = _arm_run(4, p99=100), _arm_run(8, p99=100)
    concurrency.arm = replace(
        concurrency.arm,
        load=LoadPlan(
            request_rate=float("inf"),
            max_concurrency=8,
            duration_s=1,
        ),
    )
    for execution in [rate, concurrency]:
        execution.slo = evaluate_slo(
            execution,
            [
                SloAssertion(
                    "p99_ms",
                    "lt",
                    1000,
                    window=WindowSelector(kind="hold"),
                )
            ],
        )
    assert slo_aware_capacity([rate, concurrency]) == {"w2|rate": 4, "w2|concurrency": 8}
