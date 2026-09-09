"""Unified metric model: per_request metrics (Outcome.metrics → distribution),
the time_sampled probe side tagged into the same registry, SLO addressing, and
the ttft / on_frame plumbing in stream_sse."""

import json

import httpx
import pytest
from spec_case.model import Case

from perf_harness import Engine, Experiment, Service
from perf_harness.config import load_experiment
from perf_harness.drive.load import LoadProfile, Schedule
from perf_harness.drive.workload import Workload, stream_sse
from perf_harness.metric import MetricFamily
from perf_harness.model import Outcome, ResourceProfile, SloAssertion
from perf_harness.observe import ClientProbe
from perf_harness.report import write_report
from perf_harness.slo import evaluate_slo


class _MetricWL(Workload):
    """Records two per_request metrics on every fire (constant values → known pctls)."""

    name = "metricwl"

    async def fire(self, ctx):
        return Outcome(
            status=200,
            duration_ms=10.0,
            events=1,
            metrics={"ttft_ms": 5.0, "first_answer_ms": 30.0},
        )


async def _one_trial():
    exp = Experiment(
        service=Service("m", base_url="http://127.0.0.1:0"),
        workload=_MetricWL(),
        resources=[ResourceProfile(workers=2)],
        loads=[LoadProfile(model="closed", schedule=Schedule.ramp_hold(3, 0.0, 0.2))],
        probes=[ClientProbe()],
    )
    return (await Engine(exp).run()).trials[0]


async def test_per_request_metrics_aggregate_to_distribution():
    r = await _one_trial()
    assert "ttft_ms" in r.measurement.request.metrics
    ms = r.measurement.request.metrics["ttft_ms"]
    # constant 5.0 across every sent request → n matches, all percentiles == 5.0
    assert ms.n == r.measurement.request.n
    assert ms.mean == ms.p50 == ms.p95 == 5.0
    assert r.measurement.request.metrics["first_answer_ms"].p95 == 30.0


class _DeclaringWL(Workload):
    """Declares one metric's unit/source; leaves a dynamic one undeclared."""

    name = "declwl"

    async def fire(self, ctx):
        return Outcome(
            status=200,
            duration_ms=10.0,
            events=1,
            metrics={"prompt_tokens": 128.0, "first_answer_ms": 50.0},
        )

    def describe(self):
        # prompt_tokens: suffix convention can't unit it, and it's server-sourced
        return [
            MetricFamily(
                name="prompt_tokens",
                unit="tok",
                side="request",
                value_kind="distribution",
                source="server",
            )
        ]


async def test_workload_describe_overrides_inference_partially():
    exp = Experiment(
        service=Service("d", base_url="http://127.0.0.1:0"),
        workload=_DeclaringWL(),
        resources=[ResourceProfile()],
        loads=[LoadProfile(model="closed", schedule=Schedule.ramp_hold(2, 0.0, 0.2))],
    )
    reg = (await Engine(exp).run()).trials[0].metrics
    # declared descriptor wins (suffix would give unit="" / source=client)
    assert reg["prompt_tokens"].unit == "tok"
    assert reg["prompt_tokens"].source == "server"
    # undeclared dynamic key still gets the inferred descriptor — declaration is partial
    assert reg["first_answer_ms"].unit == "ms"
    assert reg["first_answer_ms"].source == "client"


async def test_unified_registry_tags_both_kinds():
    r = await _one_trial()
    reg = r.metrics
    # request side: descriptor with side/value_kind/source/unit
    assert reg["ttft_ms"].side == "request"
    assert reg["ttft_ms"].value_kind == "distribution"
    assert reg["ttft_ms"].source == "client"
    assert reg["ttft_ms"].unit == "ms"  # inferred from the _ms suffix
    # resource side (ClientProbe summary) folded into the SAME registry, tagged
    resource = [m for m in reg.values() if m.side == "resource"]
    assert resource and all(m.source == "client" for m in resource)


async def test_slo_targets_per_request_metric_percentile():
    r = await _one_trial()

    def check(metric, op, thr):
        return evaluate_slo(r, [SloAssertion(metric=metric, op=op, threshold=thr)])[0]

    assert check("ttft_ms.p95", "lt", 10).passed  # 5 < 10
    assert not check("ttft_ms.p95", "lt", 1).passed  # 5 !< 1
    # an absent metric is SKIPPED (three-state) — never a silent failure, and never a
    # silent pass either (a skip is not green)
    skipped = check("nonexistent_ms.p95", "lt", 1)
    assert skipped.observed is None and skipped.skipped and not skipped.passed


async def test_report_shows_metric_columns_and_header_tooltips(tmp_path):
    r = await _one_trial()
    write_report([r], str(tmp_path))
    summary = (tmp_path / "summary.csv").read_text()
    assert "ttft_ms.p95" in summary and "first_answer_ms.p50" in summary
    # the metric meaning lives in the HTML column-header tooltip (no legend dump)
    html = (tmp_path / "report.html").read_text()
    assert "data-tip" in html  # header tooltips present
    assert "time to first SSE byte" in html  # ttft_ms description surfaced
    md = (tmp_path / "report.md").read_text()
    assert "统一模型" not in md  # the legend line is gone
    assert "| service |" not in md  # service is no longer a table column


async def test_facet_table_carries_per_request_metrics(tmp_path):
    # per_request metrics are sliceable → the by-facet md table must show them
    # (not just the overall §1 table); CSV already did.
    exp = Experiment(
        service=Service("m", base_url="http://127.0.0.1:0"),
        workload=_MetricWL(),
        resources=[ResourceProfile(workers=2)],
        loads=[LoadProfile(model="closed", schedule=Schedule.ramp_hold(3, 0.0, 0.2))],
        cases=[Case(id="a", input={}, facets={"difficulty": "simple"})],
    )
    r = (await Engine(exp).run()).trials[0]
    write_report([r], str(tmp_path))
    md = (tmp_path / "report.md").read_text()
    facet_section = md.split("## 2.")[1]
    # per_request metrics show as a merged column (`ttft_ms` cell = "p50/p95"), not flat
    assert "ttft_ms" in facet_section


async def test_stream_sse_records_ttft_and_delivers_frames():
    body = (
        b'data: {"event":"start"}\n\n' b'data: {"event":"message"}\n\n' b'data: {"event":"end"}\n\n'
    )

    def handler(_request):
        return httpx.Response(200, content=body, headers={"content-type": "text/event-stream"})

    seen: list[bytes] = []
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        out = await stream_sse(
            client, "http://x/api", json={}, on_frame=lambda _t, d: seen.append(d)
        )

    assert out.status == 200
    assert "ttft_ms" in out.metrics and out.metrics["ttft_ms"] >= 0
    assert out.events == 3  # three data: frames
    assert [json.loads(d)["event"] for d in seen] == ["start", "message", "end"]


def test_config_accepts_per_request_metric_slo(tmp_path):
    cfg = tmp_path / "c.yaml"
    cfg.write_text(
        "name: x\n"
        'service: { name: s, base_url: "http://x" }\n'
        "resources: [ {} ]\n"
        "workload: { name: mock }\n"
        "load: { model: closed, levels: [1], ramp_s: 0, steady_s: 0.2 }\n"
        "slo:\n"
        "  - { metric: ttft_ms.p95, lt: 2000 }\n"
        "  - { metric: error_rate, lt: 0.01 }\n"
    )
    experiment, _ = load_experiment(str(cfg))
    assert {a.metric for a in experiment.slo} == {"ttft_ms.p95", "error_rate"}


class _BadKindWL(Workload):
    """Declares ttft_ms as a resource-side gauge — wrong: a Workload only does request side."""

    name = "badkind"

    async def fire(self, ctx):  # pragma: no cover - never fired
        return Outcome(status=200, duration_ms=1.0)

    def describe(self):
        return [MetricFamily("ttft_ms", "ms", "resource", "gauge")]


class _ShadowWL(Workload):
    """Tries to declare a builtin derived request.* family (the engine owns those)."""

    name = "shadow"

    async def fire(self, ctx):  # pragma: no cover - never fired
        return Outcome(status=200, duration_ms=1.0)

    def describe(self):
        return [MetricFamily("request.duration_ms", "ms", "request", "distribution")]


def test_producer_contract_fails_fast():
    # a Workload may only declare request-side distributions
    from perf_harness.config import _validate_producers

    with pytest.raises(ValueError, match="request-side"):
        _validate_producers([], _BadKindWL())
    # …and may not shadow a builtin derived request.* family
    with pytest.raises(ValueError, match="builtin"):
        _validate_producers([], _ShadowWL())


def test_config_rejects_bad_metric_stat(tmp_path):
    cfg = tmp_path / "c.yaml"
    cfg.write_text(
        "name: x\n"
        'service: { name: s, base_url: "http://x" }\n'
        "resources: [ {} ]\n"
        "workload: { name: mock }\n"
        "load: { model: closed, levels: [1], ramp_s: 0, steady_s: 0.2 }\n"
        "slo:\n"
        "  - { metric: ttft_ms.p42, lt: 1 }\n"  # p42 is not a real stat
    )
    try:
        load_experiment(str(cfg))
    except ValueError as e:
        assert "metric" in str(e)
    else:
        raise AssertionError("expected ValueError for bad metric stat")
