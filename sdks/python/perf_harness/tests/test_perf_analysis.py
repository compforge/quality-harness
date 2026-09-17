"""analysis lenses — deterministic observations over hand-built arm_runs (precise
numbers), plus the analyze_run end-to-end over a written run dir."""

from dataclasses import replace

import pytest

from perf_harness.analysis import analyze, analyze_run, render_text
from perf_harness.analysis.base import linfit
from perf_harness.comparison import comparison_groups
from perf_harness.drive.load import LoadPlan, Stage
from perf_harness.metric import GaugeSummary, MetricFamily, series_id
from perf_harness.model import (
    Arm,
    ArmRun,
    ArmStop,
    PhaseError,
    RequestStats,
    ResourceProfile,
    Run,
    Sample,
    Series,
    Window,
)

_REGISTRY = {
    "top.cpu_m": MetricFamily("top.cpu_m", "millicores", "resource", "gauge", "k8s"),
    "top.mem_mi": MetricFamily("top.mem_mi", "MiB", "resource", "gauge", "k8s"),
    "limits.cpu_request": MetricFamily(
        "limits.cpu_request", "millicores", "resource", "gauge", "k8s"
    ),
    "limits.cpu_limit": MetricFamily("limits.cpu_limit", "millicores", "resource", "gauge", "k8s"),
    "first_byte_ms": MetricFamily("first_byte_ms", "ms", "per_request", "distribution", "client"),
}


def _stats(n, rps, p50, p95, p99, caveats=frozenset()) -> RequestStats:
    return RequestStats(
        n=n, n_ok=n, throughput_rps=rps, p50_ms=p50, p95_ms=p95, p99_ms=p99,
        error_rate=0.0, error_breakdown={}, caveats=caveats,
    )  # fmt: skip


def _arm_run(level, stats, cpu_peaks: dict[str, float], breaker=None) -> ArmRun:
    pm = {}
    for svc, peak in cpu_peaks.items():
        pm[series_id("top.cpu_m", {"service": svc})] = GaugeSummary(
            last=peak, mean=peak * 0.9, peak=peak
        )
        pm[series_id("limits.cpu_request", {"service": svc})] = GaugeSummary(
            last=1000, mean=1000, peak=1000
        )
        pm[series_id("limits.cpu_limit", {"service": svc})] = GaugeSummary(
            last=2000, mean=2000, peak=2000
        )
    resources = ResourceProfile(workers=2)
    load = LoadPlan(
        request_rate=float("inf"),
        max_concurrency=level,
        duration_s=(0.0 + 45.0),
        abort_on_error_rate=breaker,
        breaker_min_n=10,
    )
    return ArmRun(
        id=f"{resources.label()}|{load.label()}",
        service="chat",
        arm=Arm(f"{resources.label()}|{load.label()}", resources, load),
        windows=[
            Window(
                "measurement",
                "measurement",
                "measurement",
                0.0,
                45.0,
                True,
                request=stats,
                by_facet={"difficulty": {"simple": stats}},
                probe_metrics=pm,
            )
        ],
        series={},
        metrics=dict(_REGISTRY),
    )


def _sweep() -> Run:
    """2/4/8 sweep shaped like the real dev press: linear 2→4, knee at 8, planit cpu
    hotter than chat, sub-100 n everywhere, p95==p99 at level 2."""
    arm_runs = [
        _arm_run(2, _stats(17, 0.38, 5691, 6901, 6901), {"chat": 123, "planit": 120}, 0.10),
        _arm_run(4, _stats(34, 0.76, 6045, 7359, 7445), {"chat": 147, "planit": 224}, 0.10),
        _arm_run(8, _stats(52, 1.16, 7620, 9258, 10292), {"chat": 228, "planit": 362}, 0.10),
    ]
    return Run("rid", "exp", "t", "chat", executions=arm_runs)


def _titles(obs, analyzer=None, kind=None):
    return [
        o.title
        for o in obs
        if (analyzer is None or o.analyzer == analyzer) and (kind is None or o.kind == kind)
    ]


def test_linfit_basic():
    fit = linfit([(2, 120.0), (4, 224.0), (8, 362.0)])
    assert fit is not None
    slope, _ = fit
    assert 38 < slope < 42  # ~40 m/level — the planit sensitivity from the dev press
    assert linfit([(2, 1.0)]) is None  # underdetermined


def test_capacity_finds_the_knee():
    obs = analyze(_sweep())
    flags = _titles(obs, "capacity", "flag")
    # 2→4 is perfectly linear (no flag); 4→8 per-user throughput drops ~24% → knee
    assert len(flags) == 1 and "4" in flags[0] and "8" in flags[0]


def test_resource_headroom_slope_and_extrapolation():
    obs = analyze(_sweep())
    facts = [o for o in obs if o.analyzer == "resource" and o.kind == "fact"]
    planit_slope = next(
        o
        for o in facts
        if o.evidence.get("service") == "planit" and "slope_per_level" in o.evidence
    )
    assert 38 < planit_slope.evidence["slope_per_level"] < 42
    # linear extrapolation reaches request(1000m) well before limit(2000m)
    assert 20 < planit_slope.evidence["level_at_request"] < 30
    assert planit_slope.evidence["level_at_request"] < planit_slope.evidence["level_at_limit"]
    # headroom facts carry pct-of-limit for every service at the top level
    chat_head = next(
        o for o in facts if o.evidence.get("service") == "chat" and "pct_of_limit" in o.evidence
    )
    assert chat_head.evidence["pct_of_limit"] == 11.4  # 228/2000


def test_resource_flags_idle_service():
    run = _sweep()
    for t in run.arm_runs:  # executor pinned at 5m across all levels
        t.measurement.probe_metrics[series_id("top.cpu_m", {"service": "executor"})] = GaugeSummary(
            last=5, mean=5, peak=5
        )
        t.measurement.probe_metrics[series_id("limits.cpu_request", {"service": "executor"})] = (
            GaugeSummary(last=1000, mean=1000, peak=1000)
        )
    flags = _titles(analyze(run), "resource", "flag")
    assert any("executor" in t and "未被压到" in t for t in flags)


def test_resource_flags_memory_growth():
    run = _sweep()
    sid = series_id("top.mem_mi", {"service": "chat"})
    run.arm_runs[-1].series[sid] = Series(
        metric=sid, unit="MiB", samples=[Sample(0.0, 1000.0), Sample(50.0, 1200.0)]
    )  # +20% within one arm_run
    flags = _titles(analyze(run), "resource", "flag")
    assert any("增长" in t and "soak" in t for t in flags)


def test_latency_adequacy_flags_but_no_false_divergence():
    obs = analyze(_sweep())
    flags = _titles(obs, "latency", "flag")
    # p99 grew 49% vs p50 34% — under the 2× bar, so the queueing signature must NOT fire
    assert not any("尾部发散" in t for t in flags)
    assert any("p95==p99" in t for t in flags)  # level 2 exhausted tail
    assert sum("观测极值" in t for t in flags) == 3  # n=17/34/52 all < 100


def test_latency_flags_real_tail_divergence():
    arm_runs = [
        _arm_run(2, _stats(200, 1.0, 1000, 1400, 1500), {"chat": 100}),
        _arm_run(8, _stats(200, 3.0, 1100, 3500, 8000), {"chat": 200}),
    ]
    flags = _titles(analyze(Run("r", "e", "t", "chat", executions=arm_runs)), "latency", "flag")
    # p50 +10% but p99 +433% → tail diverges far faster than the median
    assert any("尾部发散" in t for t in flags)


def test_validity_breaker_reachability_and_facet_coverage():
    obs = analyze(_sweep())
    flags = _titles(obs, "validity", "flag")
    # at 0.38 rps, min_n=10 takes ~26s of a 45s window (>50%) → reachability flag
    assert any("熔断起判" in t for t in flags)
    assert any("difficulty" in t and "simple" in t for t in flags)


async def test_analyze_run_end_to_end(tmp_path):
    # reuse the runio round-trip rig: write a real run dir, then analyze offline
    from perf_harness.tests.test_perf_runio import _run

    _, run_dir = await _run(tmp_path)
    obs = analyze_run(str(run_dir))
    assert obs and any(o.analyzer == "latency" for o in obs)
    text = render_text(obs)
    assert "本次压测的有效性" in text or "延迟形态" in text


def test_validity_flags_probe_errors():
    from perf_harness.model import ProbeErrors

    run = _sweep()
    run.arm_runs[-1].probe_errors = {
        "metrics.chat": ProbeErrors(failures=3, ticks=10, last="HTTPStatusError('500')")
    }
    flags = _titles(analyze(run), "validity", "flag")
    assert any("观测断档" in t and "metrics.chat" in t and "3/10" in t for t in flags)


def test_phase_error_is_diagnostic_not_a_curve_point():
    run = _sweep()
    broken = run.arm_runs[1]
    broken.stop = ArmStop(reason="aborted")
    broken.measurement.complete = False
    broken.phase_errors = [PhaseError("setup", "RuntimeError", "testbed unavailable")]

    grouped = comparison_groups(run.arm_runs)
    assert broken not in grouped[0][1]

    flags = _titles(analyze(run), "validity", "flag")
    assert any("执行异常" in title and "setup" in title for title in flags)
    assert not any("提前停止" in title and broken.label() in title for title in flags)


def test_phase_error_after_complete_measurement_preserves_curve_point():
    for phase in ("deactivate", "cooldown", "cleanup"):
        run = _sweep()
        completed = run.arm_runs[1]
        completed.phase_errors = [PhaseError(phase, "RuntimeError", f"{phase} failed")]

        grouped = comparison_groups(run.arm_runs)
        assert completed in grouped[0][1]

        flags = _titles(analyze(run), "validity", "flag")
        assert any(
            "执行异常" in title and phase in title and "保留性能曲线点" in title for title in flags
        )


def _rate_arm(rate, cap, cpu):
    result = _arm_run(cap, _stats(200, rate, 10, 20, 30), {"chat": cpu})
    load = LoadPlan(request_rate=rate, max_concurrency=cap, duration_s=45)
    result.arm = Arm(f"rate-{rate}-cap-{cap}", result.arm.resources, load)
    result.id = result.arm.id
    return result


def test_two_dimensional_load_slices_are_shared_by_analysis_report_and_capacity(tmp_path):
    import csv
    from pathlib import Path

    from perf_harness.model import SloAssertion, SloCheck
    from perf_harness.report import write_report
    from perf_harness.report.render import _curve_groups
    from perf_harness.slo import slo_aware_capacity

    rows = [
        _rate_arm(10, 1, 110),
        _rate_arm(20, 1, 120),
        _rate_arm(10, 10, 100),
        _rate_arm(20, 10, 200),
    ]
    groups = comparison_groups(rows)
    assert [[r.arm.load.max_concurrency for r in group] for _, group in groups] == [
        [1, 1],
        [10, 10],
    ]
    assert _curve_groups(rows) == groups
    run = Run("two-axes", "exp", "t", "chat", executions=rows)
    slopes = [o for o in analyze(run) if "points" in o.evidence and "slope_per_level" in o.evidence]
    assert sorted(o.evidence["slope_per_level"] for o in slopes) == [1, 10]
    assert all(o.evidence["conditions"]["scan_axis"] == "request_rate" for o in slopes)
    assert all("requests/s" in o.title and "并发触" not in o.title for o in slopes)
    # A cap-only sweep at the same rate is a set of points, not a rate curve.
    same_rate = [rows[1], rows[3]]
    assert _curve_groups(same_rate) == []
    assert not any("points" in o.evidence for o in analyze(replace(run, executions=same_rate)))
    for r in rows:
        hold = replace(r.measurement, id="hold", kind="hold", target_level=r.arm.load.request_rate)
        r.windows.append(hold)
        r.slo = [SloCheck(SloAssertion("p99_ms", "lt", 100), 30, "pass", "hold")]
    capacities = slo_aware_capacity(rows)
    assert len(capacities) == 2 and list(capacities.values()) == [20, 20]
    assert any("max_concurrency=1|" in label for label in capacities)
    assert any("max_concurrency=10|" in label for label in capacities)
    paths = write_report(rows, str(tmp_path))
    html = Path(paths["report_html"]).read_text()
    assert "request_rate (requests/s)" in html
    assert "peak_request_rate" in html and "peak_max_concurrency" in html
    with Path(paths["summary"]).open() as stream:
        summary = list(csv.DictReader(stream))
    assert [(r["peak_request_rate"], r["peak_max_concurrency"]) for r in summary] == [
        ("10", "1"),
        ("20", "1"),
        ("10", "10"),
        ("20", "10"),
    ]


@pytest.mark.parametrize(
    "changes",
    [
        {"arrival": "poisson"},
        {"seed": 2},
        {"warmup_s": 1},
        {"duration_s": 46},
        {"drain_timeout_s": 1},
        {"abort_on_error_rate": 0.1},
        {"stages": (Stage(20, 20, 1), Stage(25, 20, 10))},
        {"request_rate": 0, "stages": (Stage(45, 20, 10, "ramp"),)},
    ],
)
def test_fixed_conditions_and_full_stage_history_are_not_erased(changes):
    first, second = _rate_arm(10, 10, 10), _rate_arm(20, 10, 20)
    second.arm = replace(second.arm, load=replace(second.arm.load, **changes))
    assert len(comparison_groups([first, second])) == 2


def test_comparison_uses_resource_values_not_only_display_labels():
    first, second = _rate_arm(10, 10, 10), _rate_arm(20, 10, 20)
    second.arm = replace(
        second.arm, resources=replace(second.arm.resources, extra={"env": "other"})
    )
    assert len(comparison_groups([first, second])) == 2


def test_proportional_stage_shapes_remain_comparable():
    first, second = _rate_arm(10, 10, 10), _rate_arm(20, 10, 20)
    for r in [first, second]:
        rate = r.arm.load.request_rate
        r.arm = replace(
            r.arm,
            load=replace(
                r.arm.load,
                request_rate=0,
                stages=(Stage(20, rate, 10, "ramp"), Stage(25, rate, 10)),
            ),
        )
    assert len(comparison_groups([first, second])) == 1
