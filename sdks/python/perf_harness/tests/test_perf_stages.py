import asyncio

import pytest

from perf_harness import (
    Engine,
    Experiment,
    LoadPlan,
    Outcome,
    ResourceProfile,
    Runner,
    Service,
    Stage,
)


def test_ramp_uses_explicit_units_and_integrated_arrival_clock():
    load = LoadPlan(
        request_rate=0,
        max_concurrency=2,
        duration_s=3,
        stages=(Stage(2, 20, 4, "ramp"), Stage(1, 20, 4)),
    )
    assert load.target(1) == (10, 3)
    assert load.arrival_time(5) == pytest.approx(1)
    assert load.arrival_time(20) == pytest.approx(2)
    assert load.arrival_time(30) == pytest.approx(2.5)
    assert load.arrival_time(40) == float("inf")


async def test_downscale_drains_instead_of_cancelling_and_preserves_windows():
    active = peak = cancelled = 0

    class Slow(Runner):
        async def fire(self, ctx):
            nonlocal active, peak, cancelled
            active += 1
            peak = max(peak, active)
            try:
                await asyncio.sleep(0.08)
            except asyncio.CancelledError:
                cancelled += 1
                raise
            finally:
                active -= 1
            return Outcome(status=200, duration_ms=80)

    load = LoadPlan(
        request_rate=float("inf"),
        max_concurrency=4,
        duration_s=0.12,
        stages=(
            Stage(0.02, float("inf"), 4, name="same"),
            Stage(0.10, float("inf"), 1, name="same"),
        ),
        drain_timeout_s=0.2,
    )
    arm = (
        await Engine(
            Experiment(
                service=Service("mock"), runner=Slow(), resources=[ResourceProfile()], loads=[load]
            )
        ).run()
    ).arm_runs[0]
    assert peak == 4 and active == 0 and cancelled == 0
    assert [w.id for w in arm.windows if w.kind == "hold"] == ["stage-0", "stage-1"]
    assert len([r for r in arm.requests if r.dispatched_at < 0.02]) == 4
    assert not [r for r in arm.requests if 0.02 <= r.dispatched_at < 0.075]
    first = next(w for w in arm.windows if w.id == "stage-0")
    assert first.request.n == 4 and first.request.completed == 0 and first.request.inflight_end == 4
    assert arm.measurement.request.n > arm.measurement.request.completed
    assert next(w for w in arm.windows if w.kind == "drain").request.completed > 0


@pytest.mark.parametrize(
    "changes",
    [
        {"max_concurrency": 0},
        {"max_concurrency": 1.5},
        {"request_rate": float("nan")},
        {"duration_s": 0},
        {"warmup_s": 2},
        {"drain_timeout_s": -1},
    ],
)
def test_invalid_load_rejected(changes):
    with pytest.raises(ValueError):
        LoadPlan(**{"request_rate": 4, "max_concurrency": 2, "duration_s": 1, **changes})
