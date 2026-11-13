import json
import math

import pytest
from harness_common import Operation, OperationRun

from perf_harness import (
    Arm,
    ArmRun,
    Engine,
    Experiment,
    HistogramQuantile,
    LoadPlan,
    MockRunner,
    Outcome,
    PhaseRun,
    ResourceProfile,
    Service,
    TimeRange,
    Warmup,
)
from perf_harness.metric.reduce import reduce_requests
from perf_harness.observe.histogram import histogram_summary
from perf_harness.records import RequestRecord
from perf_harness.runio import _summary_from, _summary_json
from perf_harness.windows import traffic_windows


def test_long_request_crosses_hold_without_clipping_its_duration():
    service = Service("example")
    arm = ArmRun(
        id="one",
        service="example",
        arm=Arm("one", ResourceProfile(), LoadPlan(request_rate=1, max_inflight=1)),
        windows=[],
        series={},
    )
    arm.requests = [
        RequestRecord(
            "request",
            "case",
            50,
            50,
            dispatched_at=50,
            finished_at=1050,
            state="finished",
            operation_run_id="request",
        )
    ]
    arm.operation_runs = [
        OperationRun(
            "request",
            service,
            Operation("stream", service_name="example"),
            Outcome(status=200, duration_ms=1_000_000),
        )
    ]
    hold = reduce_requests(arm, 0, 120)
    drain = reduce_requests(arm, 120, 1100)
    assert (hold.dispatched, hold.completed, hold.n, hold.p95_ms) == (1, 0, 1, 1_000_000)
    assert (drain.dispatched, drain.completed, drain.n) == (0, 1, 0)
    arm.requests[0].state = "interrupted"
    assert reduce_requests(arm, 0, 120).n == 0


def test_traffic_ranges_exclude_setup_cleanup_and_preserve_custom_bounds():
    phases = [
        PhaseRun(kind, kind, kind, start, end, True)
        for kind, start, end in (
            ("setup", -30, 0),
            ("warmup", 0, 10),
            ("hold", 10, 130),
            ("cooldown", 130, 1100),
            ("cleanup", 1100, 1200),
        )
    ]
    windows = traffic_windows(
        phases, [TimeRange("middle", 50, 150), TimeRange("partial", 1000, 1300)]
    )
    assert [(w.kind, w.phase) for w in windows[:4]] == [
        ("full", None),
        ("phase", "warmup"),
        ("phase", "hold"),
        ("phase", "cooldown"),
    ]
    assert (windows[0].start_s, windows[0].end_s) == (0, 1100)
    assert (windows[4].start_s, windows[4].end_s, windows[4].complete) == (50, 150, True)
    assert not windows[5].complete


def buckets(finite_count, total=100):
    return {
        "resultType": "vector",
        "result": [
            {"metric": {"le": "5"}, "value": [1, "50"]},
            {"metric": {"le": "10"}, "value": [1, str(finite_count)]},
            {"metric": {"le": "+Inf"}, "value": [1, str(total)]},
        ],
    }


def test_histogram_overflow_is_explicit_and_json_safe():
    overflow = histogram_summary(buckets(90), HistogramQuantile())
    assert overflow.value == math.inf
    assert overflow.histogram == {
        "quantile": 0.95,
        "sample_count": 100,
        "max_finite_bucket": 10,
        "overflow": True,
    }
    saved = json.loads(json.dumps(_summary_json(overflow), allow_nan=False))
    assert saved["value"] == "+Inf"
    assert _summary_from(saved) == overflow
    # Exactly the finite boundary is not proof of overflow.
    finite = histogram_summary(buckets(95), HistogramQuantile())
    assert finite.value == 10 and not finite.histogram["overflow"]
    assert histogram_summary(buckets(100), HistogramQuantile(scale=0.001)).value == pytest.approx(
        0.0095
    )


@pytest.mark.parametrize(
    "data",
    [
        buckets(110),
        {"resultType": "vector", "result": []},
        {"resultType": "scalar", "result": [1, "10"]},
    ],
)
def test_histogram_rejects_insufficient_or_invalid_bucket_evidence(data):
    with pytest.raises(ValueError):
        histogram_summary(data, HistogramQuantile())


async def test_five_serial_phases_and_separate_traffic_ranges():
    run = await Engine(
        Experiment(
            service=Service("example"),
            runner=MockRunner(base_ms=1),
            resources=[ResourceProfile()],
            loads=[
                LoadPlan(request_rate=2, max_inflight=10, warmup=Warmup(step_s=0.01), hold_s=0.02)
            ],
        )
    ).run()
    arm = run.arm_runs[0]
    assert [p.kind for p in arm.phases] == ["setup", "warmup", "hold", "cooldown", "cleanup"]
    assert all(a.end_s <= b.start_s for a, b in zip(arm.phases, arm.phases[1:], strict=False))
    assert [w.phase for w in arm.windows if w.kind == "phase"] == ["warmup", "hold", "cooldown"]
    assert arm.full.start_s == arm.phases[1].start_s
    assert arm.full.end_s == arm.phases[-2].end_s
