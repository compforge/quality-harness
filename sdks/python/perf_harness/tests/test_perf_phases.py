import asyncio
import json
from pathlib import Path

import pytest

from perf_harness import (
    Engine,
    Experiment,
    LoadPlan,
    Outcome,
    ResourceProfile,
    Runner,
    Service,
    Warmup,
)
from perf_harness.drive.controller import LoadController
from perf_harness.runio import load_run, write_run_data

FIXTURE = Path(__file__).parents[4] / "conformance/perf/fixtures/phased-load.json"


@pytest.mark.parametrize("scenario", json.loads(FIXTURE.read_text()), ids=lambda s: s["name"])
def test_phase_contract(scenario):
    values = scenario["load"]
    load = LoadPlan(**{**values, "warmup": Warmup(**values["warmup"])})
    controller = LoadController(load)
    for step in scenario["steps"]:
        controller.advance(step["at"], step["inflight"])
        assert controller.phase == step["phase"]
        assert controller.target(step["at"])[0] == step["rate"]
    assert controller.hold_start_s == scenario["hold_start_s"]
    assert controller.end_s == scenario["end_s"]
    hold = next(w for w in controller.windows if w.kind == "hold")
    assert hold.complete and hold.duration_s == 60


def test_defaults_and_bounded_configuration():
    load = LoadPlan(request_rate=4, max_inflight=100)
    assert load.hold_s == 60
    assert [s.request_rate for s in load.planned_stages] == [1, 2, 4]
    assert [s.kind for s in load.planned_stages] == ["warmup", "warmup", "hold"]
    for value in (-1, float("inf"), float("nan")):
        with pytest.raises(ValueError):
            Warmup(step_s=value)


async def test_hold_refills_at_target_and_stops_before_cooldown(tmp_path):
    active = peak = 0
    cleaned = False

    class Slow(Runner):
        async def fire(self, ctx):
            nonlocal active, peak
            active += 1
            peak = max(peak, active)
            try:
                await asyncio.sleep(0.03)
            finally:
                active -= 1
            return Outcome(status=200, duration_ms=30)

        async def cleanup(self, ctx):
            nonlocal cleaned
            assert active == 0
            cleaned = True

    run = await Engine(
        Experiment(
            service=Service("mock"),
            runner=Slow(),
            resources=[ResourceProfile()],
            loads=[
                LoadPlan(
                    request_rate=100,
                    max_inflight=1,
                    hold_s=0.12,
                    warmup=Warmup(step_s=1),
                    cooldown_timeout_s=0.2,
                )
            ],
        )
    ).run()
    arm = run.arm_runs[0]
    warmup, hold, cooldown = [w for w in arm.windows if w.kind != "measurement"]
    assert warmup.end_reason == "inflight_limit"
    assert warmup.duration_s < 0.1
    assert hold.target_level == 100  # cap at 1 QPS does not freeze replenishment at 1 QPS
    assert hold.complete and hold.duration_s == pytest.approx(0.12)
    assert hold.limited_s > 0
    assert peak == 1 and cleaned
    assert len(arm.operation_runs) >= 3
    assert all(r.state == "finished" and r.dispatched_at < hold.end_s for r in arm.requests)
    assert cooldown.request.completed >= 1
    assert arm.measurement.request.n_dropped == 0
    write_run_data(run, tmp_path)
    restored = load_run(tmp_path).arm_runs[0]
    assert [(w.start_s, w.end_s, w.end_reason, w.limited_s) for w in restored.windows] == [
        (w.start_s, w.end_s, w.end_reason, w.limited_s) for w in arm.windows
    ]


async def test_slow_generator_does_not_burst_to_catch_up():
    import time

    calls = 0

    class StalledOnce(Runner):
        async def fire(self, ctx):
            nonlocal calls
            calls += 1
            if calls == 1:
                time.sleep(0.03)
            return Outcome(status=200, duration_ms=30 if calls == 1 else 0)

    arm = (
        await Engine(
            Experiment(
                service=Service("mock"),
                runner=StalledOnce(),
                resources=[ResourceProfile()],
                loads=[
                    LoadPlan(
                        request_rate=100, max_inflight=100, hold_s=0.08, warmup=Warmup(step_s=0)
                    )
                ],
            )
        ).run()
    ).arm_runs[0]
    starts = [r.dispatched_at for r in arm.requests]
    assert len(starts) >= 3
    assert all(b - a >= 0.008 for a, b in zip(starts, starts[1:], strict=False))
    assert arm.measurement.request.n_dropped == 0
    assert max(r.arrived_at - r.scheduled_at for r in arm.requests) >= 0.015


def test_capped_window_does_not_confirm_target_rate_capacity():
    from perf_harness.model import Arm, ArmRun, RequestStats, SloAssertion, SloCheck, Window
    from perf_harness.slo import slo_aware_capacity

    stats = RequestStats(
        n=10,
        n_ok=10,
        throughput_rps=1,
        p50_ms=10,
        p95_ms=10,
        p99_ms=10,
        error_rate=0,
        error_breakdown={},
    )
    hold = Window(
        "hold", "hold@4", "hold", 0, 60, True, target_level=4, request=stats, limited_s=10
    )
    arm = ArmRun(
        id="cap",
        service="mock",
        arm=Arm("cap", ResourceProfile(), LoadPlan(request_rate=4, max_inflight=1)),
        windows=[
            Window("measurement", "measurement", "measurement", 0, 60, True, request=stats),
            hold,
        ],
        series={},
    )
    arm.slo = [SloCheck(SloAssertion("error_rate", "lte", 0), 0, "pass", window_id="hold")]
    assert list(slo_aware_capacity([arm]).values()) == [None]
