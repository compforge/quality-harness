"""Unified metric model: per_request metrics (Outcome.metrics → distribution),
the time_sampled probe side tagged into the same registry, SLO addressing, and
the ttft / on_frame plumbing in stream_sse."""

import json

import httpx
import pytest
from spec_case.model import Case

from perf_harness import Engine, Experiment, Service
from perf_harness.config import load_experiment
from perf_harness.drive.load import LoadPlan
from perf_harness.drive.runner import Runner, stream_sse
from perf_harness.metric import MetricFamily
from perf_harness.model import Outcome, ResourceProfile, SloAssertion
from perf_harness.observe import ClientProbe
from perf_harness.report import write_report
from perf_harness.slo import evaluate_slo


class _MetricWL(Runner):
    """Records two per_request metrics on every fire (constant values → known pctls)."""

    name = "metricwl"

    async def fire(self, ctx):
        return Outcome(
            status=200,
            duration_ms=10.0,
            events=1,
            metrics={"first_byte_ms": 5.0, "first_answer_ms": 30.0},
        )


async def _one_arm_run():
    exp = Experiment(
        service=Service("m", base_url="http://127.0.0.1:0"),
        runner=_MetricWL(),
        resources=[ResourceProfile(workers=2)],
        loads=[LoadPlan(request_rate=float("inf"), max_concurrency=3, duration_s=(0.0 + 0.2))],
        probes=[ClientProbe()],
    )
    return (await Engine(exp).run()).arm_runs[0]


async def test_per_request_metrics_aggregate_to_distribution():
    r = await _one_arm_run()
    assert "first_byte_ms" in r.measurement.request.metrics
    ms = r.measurement.request.metrics["first_byte_ms"]
    # constant 5.0 across every sent request → n matches, all percentiles == 5.0
    assert ms.n == r.measurement.request.n
    assert ms.mean == ms.p50 == ms.p95 == 5.0
    assert r.measurement.request.metrics["first_answer_ms"].p95 == 30.0


class _DeclaringWL(Runner):
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


async def test_runner_describe_overrides_inference_partially():
    exp = Experiment(
        service=Service("d", base_url="http://127.0.0.1:0"),
        runner=_DeclaringWL(),
        resources=[ResourceProfile()],
        loads=[LoadPlan(request_rate=float("inf"), max_concurrency=2, duration_s=(0.0 + 0.2))],
    )
    reg = (await Engine(exp).run()).arm_runs[0].metrics
    # declared descriptor wins (suffix would give unit="" / source=client)
    assert reg["prompt_tokens"].unit == "tok"
    assert reg["prompt_tokens"].source == "server"
    # undeclared dynamic key still gets the inferred descriptor — declaration is partial
    assert reg["first_answer_ms"].unit == "ms"
    assert reg["first_answer_ms"].source == "client"


async def test_unified_registry_tags_both_kinds():
    r = await _one_arm_run()
    reg = r.metrics
    # request side: descriptor with side/value_kind/source/unit
    assert reg["first_byte_ms"].side == "request"
    assert reg["first_byte_ms"].value_kind == "distribution"
    assert reg["first_byte_ms"].source == "client"
    assert reg["first_byte_ms"].unit == "ms"  # inferred from the _ms suffix
    # resource side (ClientProbe summary) folded into the SAME registry, tagged
    resource = [m for m in reg.values() if m.side == "resource"]
    assert resource and all(m.source == "client" for m in resource)


async def test_slo_targets_per_request_metric_percentile():
    r = await _one_arm_run()

    def check(metric, op, thr):
        return evaluate_slo(r, [SloAssertion(metric=metric, op=op, threshold=thr)])[0]

    assert check("first_byte_ms.p95", "lt", 10).passed  # 5 < 10
    assert not check("first_byte_ms.p95", "lt", 1).passed  # 5 !< 1
    # an absent metric is SKIPPED (three-state) — never a silent failure, and never a
    # silent pass either (a skip is not green)
    skipped = check("nonexistent_ms.p95", "lt", 1)
    assert skipped.observed is None and skipped.skipped and not skipped.passed


async def test_report_shows_metric_columns_and_header_tooltips(tmp_path):
    r = await _one_arm_run()
    write_report([r], str(tmp_path))
    summary = (tmp_path / "summary.csv").read_text()
    assert "first_byte_ms.p95" in summary and "first_answer_ms.p50" in summary
    # the metric meaning lives in the HTML column-header tooltip (no legend dump)
    html = (tmp_path / "report.html").read_text()
    assert "data-tip" in html  # header tooltips present
    assert "time to first response byte" in html  # first_byte_ms description surfaced
    md = (tmp_path / "report.md").read_text()
    assert "统一模型" not in md  # the legend line is gone
    assert "| service |" not in md  # service is no longer a table column


async def test_facet_table_carries_per_request_metrics(tmp_path):
    # per_request metrics are sliceable → the by-facet md table must show them
    # (not just the overall §1 table); CSV already did.
    exp = Experiment(
        service=Service("m", base_url="http://127.0.0.1:0"),
        runner=_MetricWL(),
        resources=[ResourceProfile(workers=2)],
        loads=[LoadPlan(request_rate=float("inf"), max_concurrency=3, duration_s=(0.0 + 0.2))],
        cases=[Case(id="a", input={}, facets={"difficulty": "simple"})],
    )
    r = (await Engine(exp).run()).arm_runs[0]
    write_report([r], str(tmp_path))
    md = (tmp_path / "report.md").read_text()
    facet_section = md.split("## 2.")[1]
    # per_request metrics show as a merged column (`first_byte_ms` cell = "p50/p95"), not flat
    assert "first_byte_ms" in facet_section


async def test_stream_sse_records_ttft_and_delivers_frames():
    body = b'data: {"event":"start"}\n\ndata: {"event":"message"}\n\ndata: {"event":"end"}\n\n'

    def handler(_request):
        return httpx.Response(200, content=body, headers={"content-type": "text/event-stream"})

    seen: list[bytes] = []
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        out = await stream_sse(
            client, "http://x/api", json={}, on_frame=lambda _t, d: seen.append(d)
        )

    assert out.status == 200
    assert "first_byte_ms" in out.metrics and out.metrics["first_byte_ms"] >= 0
    assert out.events == 3  # three data: frames
    assert [json.loads(d)["event"] for d in seen] == ["start", "message", "end"]


def test_config_accepts_per_request_metric_slo(tmp_path):
    cfg = tmp_path / "c.yaml"
    cfg.write_text(
        "name: x\n"
        'service: { name: s, base_url: "http://x" }\n'
        "resources: [ {} ]\n"
        "runner: { name: mock }\n"
        "load: { request_rate: inf, max_concurrency: 1, duration_s: 0.2 }\n"
        "slo:\n"
        "  - { metric: first_byte_ms.p95, lt: 2000 }\n"
        "  - { metric: error_rate, lt: 0.01 }\n"
    )
    experiment, _ = load_experiment(str(cfg))
    assert {a.metric for a in experiment.slo} == {"first_byte_ms.p95", "error_rate"}


class _BadKindWL(Runner):
    """Declares first_byte_ms as a resource-side gauge — wrong: a Runner only does request side."""

    name = "badkind"

    async def fire(self, ctx):  # pragma: no cover - never fired
        return Outcome(status=200, duration_ms=1.0)

    def describe(self):
        return [MetricFamily("first_byte_ms", "ms", "resource", "gauge")]


class _ShadowWL(Runner):
    """Tries to declare a builtin derived request.* family (the engine owns those)."""

    name = "shadow"

    async def fire(self, ctx):  # pragma: no cover - never fired
        return Outcome(status=200, duration_ms=1.0)

    def describe(self):
        return [MetricFamily("request.duration_ms", "ms", "request", "distribution")]


def test_producer_contract_fails_fast():
    # a Runner may only declare request-side distributions
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
        "runner: { name: mock }\n"
        "load: { request_rate: inf, max_concurrency: 1, duration_s: 0.2 }\n"
        "slo:\n"
        "  - { metric: first_byte_ms.p42, lt: 1 }\n"  # p42 is not a real stat
    )
    try:
        load_experiment(str(cfg))
    except ValueError as e:
        assert "metric" in str(e)
    else:
        raise AssertionError("expected ValueError for bad metric stat")
