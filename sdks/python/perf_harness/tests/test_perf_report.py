from pathlib import Path

from perf_harness.drive.load import LoadProfile, Schedule
from perf_harness.metric import CounterSummary, GaugeSummary
from perf_harness.model import (
    Arm,
    PhaseError,
    RequestStats,
    ResourceProfile,
    Sample,
    Series,
    SloAssertion,
    SloCheck,
    StopSnapshot,
    TrialRecord,
    TrialStop,
    Window,
    WindowSelector,
)
from perf_harness.report import write_report


def _stats(n=100, n_ok=95, err=0.05, breakdown=None) -> RequestStats:
    return RequestStats(
        n=n,
        n_ok=n_ok,
        throughput_rps=9.5,
        p50_ms=100,
        p95_ms=200,
        p99_ms=400,
        error_rate=err,
        error_breakdown=breakdown if breakdown is not None else {"503": 5},
    )


def _trial(level=10) -> TrialRecord:
    resources = ResourceProfile(workers=2, memory="2Gi")
    load = LoadProfile(model="closed", schedule=Schedule.ramp_hold(level, 0.0, 1.0))
    stats = _stats()
    return TrialRecord(
        id=f"{resources.label()}|{load.label()}",
        service="example",
        arm=Arm(f"{resources.label()}|{load.label()}", resources, load),
        windows=[
            Window(
                "measurement",
                "measurement",
                "measurement",
                0.0,
                1.0,
                True,
                request=stats,
                by_facet={
                    "difficulty": {
                        "simple": _stats(n=70, n_ok=70, err=0.0, breakdown={}),
                        "complex": _stats(n=30, n_ok=25, err=0.17, breakdown={"503": 5}),
                    }
                },
                probe_metrics={
                    "top.mem_mi": GaugeSummary(last=1800.0, mean=1400.0, peak=1800.0),
                    "metrics.req_total": CounterSummary(total=900.0, rate=9.0),
                },
            ),
            Window("stage-0", f"hold@{level:g}", "hold", 0.0, 1.0, True, level, stats),
        ],
        series={"top.mem_mi": Series("mem_mi", "MiB", [Sample(0, 1000), Sample(5, 1800)])},
    )


def test_write_report_emits_artifacts(tmp_path):
    trial = _trial()
    trial.windows.append(
        Window(
            "cooldown",
            "cooldown",
            "cooldown",
            1.0,
            2.0,
            True,
            probe_metrics={"top.mem_mi": GaugeSummary(last=0, mean=10, peak=20)},
        )
    )
    paths = write_report([trial], str(tmp_path))
    for key in ("summary", "by_facet", "windows", "timeseries", "report"):
        assert Path(paths[key]).exists()

    summary = Path(paths["summary"]).read_text().splitlines()
    assert len(summary) == 2  # header + 1 trial
    assert "top.mem_mi.peak" in summary[0]

    md = Path(paths["report"]).read_text()
    assert "## 1. 汇总" in md
    assert "503" in md  # error taxonomy surfaces via err_top
    assert "difficulty" in md and "complex" in md  # facet breakdown rendered

    ts = Path(paths["timeseries"]).read_text()
    assert "top.mem_mi" in ts

    facet = Path(paths["by_facet"]).read_text()
    assert "difficulty" in facet and "simple" in facet and "complex" in facet

    windows = Path(paths["windows"]).read_text()
    assert "window_id" in windows and "top.mem_mi.peak" in windows
    assert "cooldown" in windows


def test_report_flags_knee(tmp_path):
    low = _trial(level=5)
    low.measurement.request.error_rate = 0.0
    high = _trial(level=40)
    high.measurement.request.error_rate = 0.11
    paths = write_report([low, high], str(tmp_path))
    md = Path(paths["report"]).read_text()
    assert "拐点" in md


def test_report_rejects_early_stop_as_capacity(tmp_path):
    trial = _trial(level=10)
    trial.stop = TrialStop(
        reason="error_rate",
        snapshot=StopSnapshot(
            at_s=34.0,
            sent=50,
            errors=7,
            error_rate=0.14,
            threshold=0.1,
        ),
    )
    assertion = SloAssertion("p99_ms", "lt", 1000)
    trial.slo = [SloCheck(assertion, observed=400, state="pass")]

    paths = write_report([trial], str(tmp_path))
    md = Path(paths["report"]).read_text()
    assert "Run 判定（trial 完整性 + SLO）" in md
    assert "FAIL" in md and "部分窗口不能确认" in md
    assert "SLO-aware 容量" in md and "—（无档达标）" in md


def test_report_distinguishes_phase_error_from_load_failure(tmp_path):
    trial = _trial(level=10)
    trial.stop = TrialStop(reason="aborted")
    trial.phase_errors = [PhaseError("setup", "RuntimeError", "target unavailable")]

    paths = write_report([trial], str(tmp_path))
    md = Path(paths["report"]).read_text()
    assert "ERROR" in md
    assert "setup: RuntimeError: target unavailable" in md
    assert "不是请求失败或 SLO fail" in md


def test_report_names_actual_window_and_fails_closed_on_cooldown_skip(tmp_path):
    trial = _trial(level=10)
    assertion = SloAssertion(
        "client.inflight.last", "lte", 0, window=WindowSelector(kind="cooldown")
    )
    trial.slo = [SloCheck(assertion, observed=None, state="skipped", window_id="cooldown")]

    paths = write_report([trial], str(tmp_path))
    md = Path(paths["report"]).read_text()
    assert "FAIL" in md
    assert "client.inflight.last [window=cooldown]" in md
    assert "WindowSelector(" not in md


def test_facet_order_sorts_ordinal(tmp_path):
    # ordered facet: simple before complex, despite alpha order complex < simple
    paths = write_report(
        [_trial()], str(tmp_path), facet_order={"difficulty": ["simple", "complex"]}
    )
    md = Path(paths["report"]).read_text()
    assert md.index("| simple ") < md.index("| complex ")


def _svc_trial(level: float) -> TrialRecord:
    """A trial with service-LABELED resource series + count gauges, the real-config
    shape: §3 response curves and §4 per-service sections key off these."""
    from perf_harness.metric import MetricFamily, series_id

    r = _trial(level=level)
    cpu = series_id("top.cpu_m", {"service": "chat"})
    lim = series_id("limits.cpu_limit", {"service": "chat"})
    r.measurement.probe_metrics = {
        cpu: GaugeSummary(last=1.0, mean=1.0, peak=100.0 + level * 20),
        lim: GaugeSummary(last=500.0, mean=500.0, peak=500.0),
    }
    r.series = {
        cpu: Series("cpu_m", "millicores", [Sample(0, 100), Sample(5, 100 + level * 20)]),
        lim: Series("cpu_limit", "millicores", [Sample(0, 500), Sample(5, 500)]),
        "client.inflight": Series("inflight", "count", [Sample(0, 0), Sample(5, level)]),
        "client.sent": Series("sent", "count", [Sample(0, 0), Sample(5, level * 9)]),
    }
    r.metrics = {
        "top.cpu_m": MetricFamily("top.cpu_m", "millicores", "resource", "gauge", "k8s"),
        "limits.cpu_limit": MetricFamily(
            "limits.cpu_limit", "millicores", "resource", "gauge", "k8s"
        ),
        "client.inflight": MetricFamily("client.inflight", "count", "resource", "gauge"),
        "client.sent": MetricFamily("client.sent", "count", "resource", "counter"),
    }
    return r


def test_response_curves_section_per_service(tmp_path):
    # ≥2 levels → §3 exists: entry sub-section (err/latency vs level) + one sub-section
    # per service whose cpu curve carries the flat limit reference line
    paths = write_report([_svc_trial(5), _svc_trial(40)], str(tmp_path))
    html = Path(paths["report_html"]).read_text()
    assert "3. 压力响应曲线" in html
    # each Heading group is its own nested collapsible <details class="sub">
    assert '<details class="sub" open><summary>请求侧（入口）</summary>' in html
    assert '<details class="sub" open><summary>chat</summary>' in html
    assert "错误率与丢弃" in html and "延迟 — " in html
    assert "chat · CPU峰值" in html and "limits.cpu_limit" in html


def test_response_curves_omitted_for_single_level(tmp_path):
    paths = write_report([_svc_trial(5)], str(tmp_path))
    html = Path(paths["report_html"]).read_text()
    assert "压力响应曲线" not in html  # one point is not a curve
    assert "4. 时间序列" in html  # the within-trial section still renders


def test_timeseries_section_left_resource_right_pressure(tmp_path):
    paths = write_report([_svc_trial(5)], str(tmp_path))
    html = Path(paths["report_html"]).read_text()
    # per-service chart: left = usage + limit line, right = dashed pressure
    # (inflight + the actual send rate derived from client.sent)
    assert "chat · CPU — " in html
    assert html.count("yAxisIndex") >= 1 and "dashed" in html
    assert "client.inflight" in html
    assert '"name": "client.sent"' in html


def _biz_trial(level: float) -> TrialRecord:
    """Service-labeled BUSINESS metrics: a scraped counter + a derived scalar — the
    observation-plane signals that must ride §3 curves / §4 rate charts like resources."""
    from perf_harness.metric import (
        CounterSummary,
        MetricFamily,
        ScalarSummary,
        series_id,
    )

    r = _svc_trial(level)
    errs = series_id("metrics.sse_errors", {"service": "chat"})
    mean = series_id("sse_ttft_mean_s", {"service": "chat"})
    r.measurement.probe_metrics[errs] = CounterSummary(
        total=level * 10, rate=level * 0.1, increase=level
    )
    r.measurement.probe_metrics[mean] = ScalarSummary(value=0.1 + level * 0.01)
    r.series[errs] = Series(
        "sse_errors", "count", [Sample(0, 0), Sample(5, level), Sample(10, level * 3)]
    )
    r.metrics["metrics.sse_errors"] = MetricFamily(
        "metrics.sse_errors", "count", "resource", "counter", "http"
    )
    r.metrics["sse_ttft_mean_s"] = MetricFamily(
        "sse_ttft_mean_s", "s", "resource", "scalar", "http"
    )
    return r


def test_response_curves_include_business_metrics(tmp_path):
    paths = write_report([_biz_trial(5), _biz_trial(40)], str(tmp_path))
    html = Path(paths["report_html"]).read_text()
    # scraped counter → "计数速率 vs 档位"; derived scalar → its own mean curve
    assert "chat · 计数速率 — " in html and "metrics.sse_errors.rate" in html
    assert "sse_ttft_mean_s.value" in html  # "did server ttft grow with pressure"


def test_response_curves_group_by_label_fanout_under_one_service(tmp_path):
    # a by:-labeled counter (ctl_requests{path=…}) must NOT explode §3 into one
    # sub-section per label value ("control//v1/…" headings) — all label values are
    # LINES in the one chart under the service's own heading
    from perf_harness.metric import CounterSummary, MetricFamily, series_id

    def trial(level: float) -> TrialRecord:
        r = _svc_trial(level)
        for path, mult in (("/v1/a", 1.0), ("/v1/b", 3.0)):
            sid = series_id("metrics.ctl_requests", {"path": path, "service": "control"})
            r.measurement.probe_metrics[sid] = CounterSummary(
                total=level * mult * 10, rate=level * mult * 0.1, increase=level * mult
            )
        r.metrics["metrics.ctl_requests"] = MetricFamily(
            "metrics.ctl_requests", "count", "resource", "counter", "http"
        )
        return r

    paths = write_report([trial(5), trial(40)], str(tmp_path))
    html = Path(paths["report_html"]).read_text()
    # one heading for the service — no per-path "control/…" headings
    assert '<details class="sub" open><summary>control</summary>' in html
    assert "<summary>control/" not in html
    # …and both paths ride the SAME rate chart as separate lines (line names live
    # inside the chart-config JSON, so the labels' quotes are JSON-escaped)
    assert "control · 计数速率 — " in html
    assert 'metrics.ctl_requests{path=\\"/v1/a\\"}.rate' in html
    assert 'metrics.ctl_requests{path=\\"/v1/b\\"}.rate' in html


def test_timeseries_plots_counter_as_tick_rate(tmp_path):
    paths = write_report([_biz_trial(5)], str(tmp_path))
    html = Path(paths["report_html"]).read_text()
    # within-trial: the cumulative counter renders as a per-tick rate line
    assert "chat · 计数速率 — w2/2Gi|closed/5c" in html


def test_limits_reference_lines_pin_their_color(tmp_path):
    # limits lines must read the SAME on every chart (limit=red, request=orange) —
    # the pinned palette is report-side display policy, not family metadata
    from perf_harness.metric import MetricFamily

    trials = [_svc_trial(5), _svc_trial(40)]
    for t in trials:
        t.metrics["limits.cpu_limit"] = MetricFamily(
            "limits.cpu_limit",
            "millicores",
            "resource",
            "gauge",
            "k8s",
        )
    paths = write_report(trials, str(tmp_path))
    html = Path(paths["report_html"]).read_text()
    # pinned on both the §3 curve and the §4 timeseries charts (itemStyle + lineStyle)
    assert html.count('"color": "#d62728"') >= 4


def test_chart_legend_carries_metric_meaning(tmp_path):
    # series names are addressing ids (metrics.sse_ok{…}) — hovering the legend must
    # reveal the family's declared description (via the injected tip formatter)
    from perf_harness.metric import MetricFamily

    t = _biz_trial(5)
    t.metrics["metrics.sse_errors"] = MetricFamily(
        "metrics.sse_errors",
        "count",
        "resource",
        "counter",
        "http",
        description="SSE streams ended with a real error",
    )
    paths = write_report([t], str(tmp_path))
    html = Path(paths["report_html"]).read_text()
    assert "RK_TIP_FORMATTER" not in html  # sentinel replaced by the real formatter
    assert "SSE streams ended with a real error" in html
    assert "悬停图例可见各指标含义" in html
