from perf_harness.model import Sample, Service
from perf_harness.observe import (
    KubectlTopProbe,
    ProbeContext,
    PrometheusProbe,
    PrometheusQuery,
    RestartProbe,
)


def test_probe_client_prefers_observer_client():
    # observation must not share the load pool; ProbeContext.probe_client routes
    # HTTP-source probes to the isolated observer client, falling back to load.
    load = object()  # sentinels — we only check identity routing
    obs = object()
    ctx = ProbeContext(
        service=Service(base_url="http://x"), client=load, t0=0.0, observer_client=obs
    )
    assert ctx.probe_client is obs
    ctx_no_obs = ProbeContext(service=Service(base_url="http://x"), client=load, t0=0.0)
    assert ctx_no_obs.probe_client is load


def test_default_summarize_gauge_mean_and_peak():
    # default (gauge) summarize → typed GaugeSummary per bare metric name
    out = KubectlTopProbe().summarize(
        {
            "mem_mi": [Sample(0, 100), Sample(1, 200)],
            "cpu_m": [Sample(0, 500), Sample(1, 700)],
        }
    )
    assert out["mem_mi"].mean == 150 and out["mem_mi"].peak == 200
    assert out["cpu_m"].mean == 600 and out["cpu_m"].peak == 700


def test_prometheus_summarize_counter_rate():
    probe = PrometheusProbe(
        queries=[
            PrometheusQuery("req_total", "sum(requests_total)", "counter"),
            PrometheusQuery("in_progress", "sum(requests_in_progress)"),
        ]
    )
    out = probe.summarize(
        {
            "req_total": [Sample(0, 100), Sample(10, 300)],  # counter → rate
            "in_progress": [Sample(0, 5), Sample(5, 8)],  # gauge → peak
        }
    )
    assert out["req_total"].rate == 20.0  # (300-100)/10
    assert out["req_total"].total == 300.0
    assert out["in_progress"].peak == 8


def test_restart_summarize_counter_total():
    out = RestartProbe().summarize({"restarts": [Sample(0, 0), Sample(5, 2)]})
    assert out["restarts"].total == 2  # counter total = last reading = run total


def test_counter_reset_uses_positive_delta_accumulation():
    # a scraped service counter resets when its pod restarts: last-first would be
    # NEGATIVE and poison rate/SLO. increase = Σ max(0, Δ) (Prometheus increase()
    # semantics) and the summary carries the counter_reset caveat.
    probe = PrometheusProbe(
        queries=[PrometheusQuery("req_total", "sum(requests_total)", "counter")]
    )
    out = probe.summarize(
        {"req_total": [Sample(0, 100), Sample(5, 160), Sample(10, 20), Sample(20, 80)]}
    )
    s = out["req_total"]
    assert s.increase == 120.0  # 60 (100→160) + 0 (reset clamped) + 60 (20→80)
    assert s.rate == 6.0  # 120 / 20s — positive, never negative
    assert "counter_reset" in s.caveats
    # …and a clean counter stays caveat-free with identical numbers as before
    clean = probe.summarize({"req_total": [Sample(0, 100), Sample(10, 300)]})
    assert clean["req_total"].increase == 200.0 and not clean["req_total"].caveats
