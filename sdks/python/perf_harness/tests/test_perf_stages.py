import asyncio
import time

import httpx
from spec_case.model import Case

from perf_harness.drive.load import LoadProfile, Schedule, Stage
from perf_harness.drive.scheduler import _fire
from perf_harness.drive.workload import MockWorkload, TrialContext, Workload
from perf_harness.engine import Engine, Experiment
from perf_harness.model import Outcome, ResourceProfile, Service
from perf_harness.observe import ClientStats, ProbeContext


def test_stage_name_overrides_auto_label():
    assert Stage(over_s=1, to_level=10, kind="hold", name="warm").label == "warm"
    assert Stage(over_s=1, to_level=10, kind="hold").label == "hold@10"


async def test_engine_reduces_one_window_per_stage():
    sched = Schedule(
        stages=(
            Stage(over_s=0.01, to_level=20, kind="ramp"),
            Stage(over_s=0.5, to_level=20, kind="hold"),
            Stage(over_s=0.01, to_level=40, kind="ramp"),
            Stage(over_s=0.5, to_level=40, kind="hold"),
        )
    )
    exp = Experiment(
        service=Service("mock", base_url="http://127.0.0.1:0"),
        workload=MockWorkload(base_ms=2),
        resources=[ResourceProfile(workers=2)],
        loads=[LoadProfile(model="open", schedule=sched)],  # warmup_s defaults to 0
    )
    r = (await Engine(exp).run()).trials[0]
    holds = {window.name: window for window in r.windows if window.kind == "hold"}
    assert set(holds) == {"hold@20", "hold@40"}
    assert holds["hold@40"].request.n > holds["hold@20"].request.n
    assert holds["hold@40"].request.throughput_rps > holds["hold@20"].request.throughput_rps


async def test_single_stage_run_still_has_a_hold_window():
    exp = Experiment(
        service=Service("mock", base_url="http://127.0.0.1:0"),
        workload=MockWorkload(base_ms=2),
        resources=[ResourceProfile(workers=2)],
        loads=[LoadProfile(model="closed", schedule=Schedule.ramp_hold(3, 0.0, 0.3))],
    )
    r = (await Engine(exp).run()).trials[0]
    holds = [window for window in r.windows if window.kind == "hold"]
    assert len(holds) == 1 and holds[0].id == "stage-0"


async def test_repeated_stage_names_remain_distinct_windows():
    exp = Experiment(
        service=Service("mock", base_url="http://127.0.0.1:0"),
        workload=MockWorkload(base_ms=2),
        resources=[ResourceProfile()],
        loads=[LoadProfile(model="open", schedule=Schedule.spike(5, 20, 0.2, 0.01, 0.2))],
    )
    trial = (await Engine(exp).run()).trials[0]
    baseline = [window for window in trial.windows if window.name == "hold@5"]
    assert [window.id for window in baseline] == ["stage-0", "stage-4"]


async def test_long_request_is_attributed_by_dispatch_time():
    class SlowWorkload(Workload):
        async def fire(self, ctx):
            await asyncio.sleep(0.05)
            return Outcome(status=200, duration_ms=50)

    async with httpx.AsyncClient() as client:
        ctx = ProbeContext(
            service=Service(base_url="http://127.0.0.1:0"),
            client=client,
            t0=time.monotonic(),
            stats=ClientStats(),
        )
        trial = TrialContext(
            service=ctx.service,
            client=client,
            run_id="run",
            resources=ResourceProfile(),
            load=LoadProfile(model="closed", schedule=Schedule.ramp_hold(1, 0.0, 0.1)),
        )
        timed = []
        await _fire(SlowWorkload(), trial, ctx, Case(id="slow", input={}), timed)

    assert timed[0][0] < 0.02
