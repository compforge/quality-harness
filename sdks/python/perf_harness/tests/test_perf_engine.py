import asyncio

import pytest
from spec_case.model import Case

from perf_harness.drive.load import LoadPlan
from perf_harness.drive.runner import ArmContext, FireContext, MockRunner, Runner
from perf_harness.engine import Engine, Experiment
from perf_harness.model import (
    ArmRun,
    Outcome,
    PhaseError,
    ResourceProfile,
    Sample,
    Service,
    SloAssertion,
    WindowSelector,
)
from perf_harness.observe import ClientProbe, FamilySpec, Probe


class _AlwaysFailWL(Runner):
    """Every fire returns 500 → judge fails → 100% error rate (drives the breaker)."""

    name = "failwl"

    async def fire(self, ctx):
        await asyncio.sleep(0.001)
        return Outcome(status=500, duration_ms=1.0)


class _RaisesWL(Runner):
    name = "raises"

    async def fire(self, ctx):
        raise RuntimeError("transport detail")


async def test_engine_smoke_offline():
    service = Service("mock", base_url="http://127.0.0.1:0")
    experiment = Experiment(
        service=service,
        runner=MockRunner(base_ms=5),
        resources=[ResourceProfile(workers=2)],
        loads=[LoadPlan(request_rate=float("inf"), max_concurrency=3, duration_s=(0.0 + 0.4))],
        probes=[ClientProbe()],
        observe_interval_s=0.05,
    )
    run = await Engine(experiment).run()

    assert run.run_id and run.service == "mock"
    assert len(run.arm_runs) == 1
    r = run.arm_runs[0]
    assert run.executions == run.arm_runs
    assert r.operation_runs
    assert r.operation_runs[0].outcome in [call.outcome for call in r.operation_runs]
    assert r.measurement.request.n > 0
    assert r.measurement.request.n_ok == r.measurement.request.n
    assert r.measurement.request.error_rate == 0.0
    assert r.measurement.request.throughput_rps > 0
    # client-side probe produced a time-series within the arm_run
    assert any(k.startswith("client.") for k in r.series)
    assert "client.inflight" in r.measurement.probe_metrics  # typed gauge summary
    assert r.measurement.probe_metrics["client.inflight"].peak is not None


async def test_engine_stamps_case_id_and_preserves_exception_detail():
    experiment = Experiment(
        service=Service("mock", base_url="http://127.0.0.1:0"),
        runner=_RaisesWL(),
        resources=[ResourceProfile()],
        loads=[LoadPlan(request_rate=float("inf"), max_concurrency=1, duration_s=(0.0 + 0.05))],
        cases=[Case(id="transport-case", input={})],
        observe_interval_s=0.01,
    )

    arm_run = (await Engine(experiment).run()).arm_runs[0]
    assert arm_run.operation_runs
    outcomes = [call.outcome for call in arm_run.operation_runs]
    assert {outcome.case_id for outcome in outcomes} == {"transport-case"}
    assert {outcome.meta["exc"] for outcome in outcomes} == {"RuntimeError"}
    assert {outcome.meta["exc_detail"] for outcome in outcomes} == {"transport detail"}


async def test_engine_sweeps_grid():
    service = Service("mock", base_url="http://127.0.0.1:0")
    experiment = Experiment(
        service=service,
        runner=MockRunner(base_ms=2),
        resources=[ResourceProfile(workers=2), ResourceProfile(workers=4)],
        loads=[LoadPlan(request_rate=float("inf"), max_concurrency=2, duration_s=(0.0 + 0.2))],
        probes=[ClientProbe()],
        observe_interval_s=0.05,
    )
    run = await Engine(experiment).run()
    # 2 constraints × 1 load = 2 arm_runs
    assert len(run.arm_runs) == 2
    assert {r.arm.resources.workers for r in run.arm_runs} == {2, 4}


def test_perf_arm_ids_must_be_unique():
    duplicate = ResourceProfile(workers=2)
    experiment = Experiment(
        service=Service("mock", base_url="http://127.0.0.1:0"),
        runner=MockRunner(base_ms=2),
        resources=[duplicate, duplicate],
        loads=[LoadPlan(request_rate=float("inf"), max_concurrency=2, duration_s=(0.0 + 0.2))],
    )
    with pytest.raises(ValueError, match="duplicate arm id"):
        experiment.resolved_arms()


def test_perf_arm_id_disambiguates_configs_with_same_display_label():
    experiment = Experiment(
        service=Service("mock", base_url="http://127.0.0.1:0"),
        runner=MockRunner(base_ms=2),
        resources=[ResourceProfile(replicas=1), ResourceProfile(replicas=2)],
        loads=[LoadPlan(request_rate=float("inf"), max_concurrency=2, duration_s=(0.0 + 0.2))],
    )
    arms = experiment.resolved_arms()
    assert len({arm.id for arm in arms}) == 2
    assert all("@" in arm.id for arm in arms)


async def test_circuit_breaker_aborts_arm_run_on_error_rate():
    # a 5s steady at concurrency 4 would normally fire thousands; the breaker should
    # trip within the first ~0.1s supervisor tick once error rate ≥ 50% (min_n=5),
    # so the arm_run ends almost immediately and is flagged aborted.
    service = Service("b", base_url="http://127.0.0.1:0")
    exp = Experiment(
        service=service,
        runner=_AlwaysFailWL(),
        resources=[ResourceProfile()],
        loads=[
            LoadPlan(
                request_rate=float("inf"),
                max_concurrency=4,
                duration_s=(0.0 + 5.0),
                abort_on_error_rate=0.5,
                breaker_min_n=5,
            )
        ],
        observe_interval_s=0.05,
    )
    run = await Engine(exp).run()
    r = run.arm_runs[0]
    # structured ArmStop (the truth); aborted is a convenience alias over stop.early
    assert r.stop.early is True
    assert not run.passed  # a partial measurement window cannot pass the run gate
    assert r.stop.reason == "error_rate"
    snap = r.stop.snapshot
    assert snap is not None  # the trip view (not post-warmup overall)
    assert snap.completed >= 5 and snap.error_rate >= 0.5 and snap.threshold == 0.5
    # fast requests drain instantly on stop → nothing force-cancelled
    assert r.stop.force_cancelled is False and r.stop.interrupted == 0
    assert r.measurement.request.error_rate == 1.0
    assert r.measurement.request.n < 3000  # stopped early — nowhere near a full 5s window


async def test_no_breaker_runs_full_window_not_aborted():
    # without abort_on_error_rate, even an all-failing runner runs the full (short)
    # window and ends with reason="deadline" — the breaker is opt-in.
    service = Service("b", base_url="http://127.0.0.1:0")
    exp = Experiment(
        service=service,
        runner=_AlwaysFailWL(),
        resources=[ResourceProfile()],
        loads=[LoadPlan(request_rate=float("inf"), max_concurrency=2, duration_s=(0.0 + 0.2))],
        observe_interval_s=0.05,
    )
    r = (await Engine(exp).run()).arm_runs[0]
    assert r.stop.early is False
    assert r.stop.reason == "deadline" and r.stop.snapshot is None
    assert r.measurement.request.error_rate == 1.0  # still all errors, just not auto-stopped


class _SlowWL(Runner):
    """Each fire sleeps 2s — long enough to still be in flight when a short arm_run ends."""

    name = "slowwl"

    async def fire(self, ctx):
        await asyncio.sleep(2.0)
        return Outcome(status=200, duration_ms=2000.0)


async def test_hard_stop_force_cancels_inflight_and_counts_interrupted():
    # drain_timeout_s=0 → at the deadline the in-flight (slow) requests are force-
    # cancelled and counted as `interrupted`, and a cancelled request NEVER becomes a
    # latency sample (overall.n stays 0). Validates the A4 invariant + the census.
    service = Service("h", base_url="http://127.0.0.1:0")
    exp = Experiment(
        service=service,
        runner=_SlowWL(),
        resources=[ResourceProfile()],
        loads=[
            LoadPlan(
                request_rate=float("inf"),
                max_concurrency=3,
                duration_s=(0.0 + 0.2),
                drain_timeout_s=0.0,
            )
        ],
        observe_interval_s=0.05,
    )
    r = (await Engine(exp).run()).arm_runs[0]
    assert r.stop.reason == "deadline"  # not a breaker — just hit the (short) deadline
    assert r.stop.force_cancelled is True
    assert r.stop.interrupted == 3 and r.stop.inflight_at_stop == 3
    assert r.measurement.request.n == 0  # the 3 cut requests are NOT latency samples


async def test_engine_open_model_smoke():
    service = Service("mock", base_url="http://127.0.0.1:0")
    experiment = Experiment(
        service=service,
        runner=MockRunner(base_ms=2),
        resources=[ResourceProfile(workers=2)],
        loads=[
            LoadPlan(request_rate=40, max_concurrency=128, duration_s=(0.2 + 0.6), warmup_s=0.2)
        ],
        probes=[ClientProbe()],
        observe_interval_s=0.05,
    )
    run = await Engine(experiment).run()
    r = run.arm_runs[0]
    # open-loop produced steady-window outcomes at roughly the held rate
    assert r.measurement.request.n > 0
    assert r.measurement.request.error_rate == 0.0
    assert r.measurement.request.throughput_rps > 0


async def test_arm_run_hooks_wrap_load_and_cooldown_only_extends_raw_series():
    state = {"post_load": False}
    events: list[str] = []
    lifecycle_contexts: list[ArmContext] = []
    fire_contexts: list[FireContext] = []

    class LifecycleRunner(Runner):
        async def setup(self, ctx: ArmContext):
            events.append("setup")
            lifecycle_contexts.append(ctx)

        async def fire(self, ctx: FireContext):
            fire_contexts.append(ctx)
            return Outcome(status=200, duration_ms=1.0)

        async def deactivate(self, ctx: ArmContext):
            events.append("deactivation")
            lifecycle_contexts.append(ctx)
            state["post_load"] = True

        async def cleanup(self, ctx: ArmContext):
            events.append("cleanup")
            lifecycle_contexts.append(ctx)

    class PhaseProbe(Probe):
        name = "phase"
        source = "test"
        families = {"post_load": FamilySpec("count")}

        async def sample(self, ctx):
            return {"post_load": float(state["post_load"])}

    exp = Experiment(
        service=Service("mock", base_url="http://127.0.0.1:0"),
        runner=LifecycleRunner(),
        resources=[ResourceProfile()],
        loads=[LoadPlan(request_rate=float("inf"), max_concurrency=1, duration_s=(0.0 + 0.12))],
        probes=[PhaseProbe()],
        observe_interval_s=0.03,
        cooldown_s=0.12,
        slo=[
            SloAssertion(
                "phase.post_load.last",
                "gte",
                1,
                window=WindowSelector(kind="cooldown"),
            )
        ],
    )
    run = await Engine(exp).run()
    arm_run = run.arm_runs[0]
    assert events == ["setup", "deactivation", "cleanup"]
    assert len({id(ctx) for ctx in lifecycle_contexts}) == 1
    assert fire_contexts and all(ctx.arm_run is lifecycle_contexts[0] for ctx in fire_contexts)
    assert arm_run.measurement.probe_metrics["phase.post_load"].peak == 0.0
    assert any(s.value == 1.0 for s in arm_run.series["phase.post_load"].samples)
    assert any(window.kind == "cooldown" for window in arm_run.windows)
    assert run.passed and arm_run.slo[0].passed and arm_run.slo[0].observed == 1.0


async def test_cleanup_runs_when_setup_fails():
    events: list[str] = []

    class BrokenSetup(Runner):
        async def setup(self, ctx):
            events.append("setup")
            raise RuntimeError("setup failed")

        async def fire(self, ctx):
            return Outcome(status=200, duration_ms=1.0)

        async def cleanup(self, ctx):
            events.append("cleanup")

    exp = Experiment(
        service=Service("mock", base_url="http://127.0.0.1:0"),
        runner=BrokenSetup(),
        resources=[ResourceProfile()],
        loads=[
            LoadPlan(request_rate=float("inf"), max_concurrency=1, duration_s=(0.0 + 0.1)),
            LoadPlan(request_rate=float("inf"), max_concurrency=2, duration_s=(0.0 + 0.1)),
        ],
    )
    run = await Engine(exp).run()
    # Execution/testbed failure stops the sweep even without abort_on_fail; the
    # cleanup result cannot prove the next Arm starts from a clean baseline.
    assert len(run.arm_runs) == 1
    arm_run = run.arm_runs[0]
    assert events == ["setup", "cleanup"]
    assert not run.passed
    assert arm_run.stop.reason == "aborted"
    assert arm_run.measurement.complete is False
    assert arm_run.measurement.request.n == 0
    assert arm_run.phase_errors == [PhaseError("setup", "RuntimeError", "setup failed")]


async def test_cleanup_error_does_not_hide_setup_error():
    class BrokenLifecycle(Runner):
        async def setup(self, ctx):
            raise RuntimeError("setup failed")

        async def fire(self, ctx):
            return Outcome(status=200, duration_ms=1.0)

        async def cleanup(self, ctx):
            raise RuntimeError("cleanup failed")

    exp = Experiment(
        service=Service("mock", base_url="http://127.0.0.1:0"),
        runner=BrokenLifecycle(),
        resources=[ResourceProfile()],
        loads=[LoadPlan(request_rate=float("inf"), max_concurrency=1, duration_s=(0.0 + 0.1))],
    )
    run = await Engine(exp).run()
    assert run.arm_runs[0].phase_errors == [
        PhaseError("setup", "RuntimeError", "setup failed"),
        PhaseError("cleanup", "RuntimeError", "cleanup failed"),
    ]


async def test_cancellation_propagates_after_cleanup():
    events: list[str] = []

    class CancelledSetup(Runner):
        async def setup(self, ctx):
            events.append("setup")
            raise asyncio.CancelledError("stop run")

        async def fire(self, ctx):
            return Outcome(status=200, duration_ms=1.0)

        async def cleanup(self, ctx):
            events.append("cleanup")

    exp = Experiment(
        service=Service("mock", base_url="http://127.0.0.1:0"),
        runner=CancelledSetup(),
        resources=[ResourceProfile()],
        loads=[LoadPlan(request_rate=float("inf"), max_concurrency=1, duration_s=(0.0 + 0.1))],
    )
    with pytest.raises(asyncio.CancelledError, match="stop run"):
        await Engine(exp).run()
    assert events == ["setup", "cleanup"]


async def test_cleanup_error_marks_completed_arm_run_as_phase_error():
    class BrokenCleanup(Runner):
        async def fire(self, ctx):
            return Outcome(status=200, duration_ms=1.0)

        async def cleanup(self, ctx):
            raise RuntimeError("cleanup failed")

    exp = Experiment(
        service=Service("mock", base_url="http://127.0.0.1:0"),
        runner=BrokenCleanup(),
        resources=[ResourceProfile()],
        loads=[LoadPlan(request_rate=float("inf"), max_concurrency=1, duration_s=(0.0 + 0.05))],
    )
    run = await Engine(exp).run()
    arm_run = run.arm_runs[0]
    assert arm_run.stop.reason == "deadline"
    assert arm_run.measurement.complete is True
    assert arm_run.phase_errors == [PhaseError("cleanup", "RuntimeError", "cleanup failed")]
    assert not run.passed


async def test_cooldown_slo_missing_data_fails_closed():
    class EmptyProbe(Probe):
        name = "empty"
        source = "test"
        families = {"value": FamilySpec("count")}

        async def sample(self, ctx):
            return {}

    exp = Experiment(
        service=Service("mock", base_url="http://127.0.0.1:0"),
        runner=MockRunner(base_ms=1),
        resources=[ResourceProfile()],
        loads=[LoadPlan(request_rate=float("inf"), max_concurrency=1, duration_s=(0.0 + 0.05))],
        probes=[EmptyProbe()],
        observe_interval_s=0.01,
        cooldown_s=0.03,
        slo=[SloAssertion("empty.value.last", "lte", 0, window=WindowSelector(kind="cooldown"))],
    )
    run = await Engine(exp).run()
    assert run.arm_runs[0].slo[0].skipped
    assert not run.passed


def test_cooldown_window_omits_stale_or_failed_probe_data():
    class ValueProbe(Probe):
        name = "value"
        source = "test"
        families = {"current": FamilySpec("count")}

        async def sample(self, ctx):
            raise NotImplementedError

    exp = Experiment(
        service=Service("mock", base_url="http://127.0.0.1:0"),
        runner=MockRunner(base_ms=1),
        resources=[ResourceProfile()],
        loads=[LoadPlan(request_rate=float("inf"), max_concurrency=1, duration_s=(0.0 + 0.05))],
        probes=[ValueProbe()],
    )
    engine = Engine(exp)
    arm = exp.resolved_arms()[0]

    stale = engine._aggregate(
        arm,
        ArmRun(id="r", service="s", arm=arm, windows=[], series={}),
        {
            ("value", "current"): [Sample(1.1, 0.0)],
            ("value", "up"): [Sample(1.1, 1.0), Sample(1.19, 1.0)],
        },
        cooldown_start_s=1.0,
        cooldown_end_s=1.2,
    )
    assert "value.current" not in stale.windows[-1].probe_metrics

    failed = engine._aggregate(
        arm,
        ArmRun(id="r", service="s", arm=arm, windows=[], series={}),
        {
            ("value", "current"): [Sample(1.1, 0.0), Sample(1.19, 0.0)],
            ("value", "up"): [Sample(1.1, 1.0), Sample(1.19, 0.0)],
        },
        cooldown_start_s=1.0,
        cooldown_end_s=1.2,
    )
    assert "value.current" not in failed.windows[-1].probe_metrics
