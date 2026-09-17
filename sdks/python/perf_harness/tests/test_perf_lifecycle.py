import asyncio
import copy
import json
import time
from pathlib import Path

import pytest

from perf_harness import (
    Engine,
    Experiment,
    LoadPlan,
    Outcome,
    RequestEvaluation,
    ResourceProfile,
    Runner,
    Service,
)
from perf_harness.drive.load import Warmup
from perf_harness.metric.reduce import reduce_requests
from perf_harness.runio import load_run, write_run_data


class BlockingRunner(Runner):
    def __init__(self, seconds=0.1):
        self.active = 0
        self.peak = 0
        self.seconds = seconds
        self.started = []

    async def fire(self, ctx):
        self.active += 1
        self.peak = max(self.peak, self.active)
        self.started.append(time.monotonic())
        try:
            await asyncio.sleep(self.seconds)
        finally:
            self.active -= 1
        return Outcome(status=200, duration_ms=self.seconds * 1000)


def experiment(runner, load, **kw):
    return Experiment(
        service=Service("mock"), runner=runner, resources=[ResourceProfile()], loads=[load], **kw
    )


async def test_rate_cap_pauses_without_drops_and_never_dispatches_after_deadline(tmp_path):
    runner = BlockingRunner(0.08)
    run = await Engine(
        experiment(
            runner,
            LoadPlan(
                warmup=Warmup(step_s=0),
                request_rate=1000,
                max_inflight=3,
                hold_s=0.06,
                cooldown_timeout_s=0.2,
            ),
        )
    ).run()
    arm = run.arm_runs[0]
    assert runner.peak == 3 and runner.active == 0
    assert len(arm.requests) == 3
    assert len(arm.operation_runs) == 3
    assert all(r.state == "finished" for r in arm.requests)
    assert all(r.dispatched_at is None or r.dispatched_at < 0.06 for r in arm.requests)
    assert arm.measurement.request.completed == 0
    assert arm.measurement.request.dispatched == 3
    assert arm.measurement.request.n == 3
    assert arm.measurement.request.n_dropped == 0
    assert arm.measurement.limited_s > 0
    write_run_data(run, tmp_path)
    restored = load_run(tmp_path).arm_runs[0]
    assert restored.requests == arm.requests
    assert restored.evaluations == arm.evaluations
    assert reduce_requests(restored, 0, 0.06) == arm.measurement.request


async def test_hard_stop_keeps_each_interrupted_call_and_does_not_judge_it():
    runner = BlockingRunner(10)

    def judge(outcome):
        raise AssertionError("interrupted calls are not completed evidence")

    run = await Engine(
        experiment(
            runner,
            LoadPlan(
                warmup=Warmup(step_s=0),
                request_rate=float("inf"),
                max_inflight=4,
                hold_s=0.03,
                cooldown_timeout_s=0,
            ),
            judge=judge,
        )
    ).run()
    arm = run.arm_runs[0]
    assert not run.passed and runner.active == 0
    assert len(arm.operation_runs) == arm.stop.interrupted == 4
    assert all(r.state == "interrupted" and r.finished_at is not None for r in arm.requests)
    assert not arm.evaluations
    assert arm.measurement.request.n == 0 and arm.measurement.request.n_interrupted == 4


async def test_offline_rejudge_preserves_raw_evidence():
    run = await Engine(
        experiment(
            BlockingRunner(0.005),
            LoadPlan(warmup=Warmup(step_s=0), request_rate=50, max_inflight=2, hold_s=0.04),
        )
    ).run()
    arm = run.arm_runs[0]
    evidence = copy.deepcopy(arm.operation_runs)
    arm.evaluations = {
        call.id: RequestEvaluation(False, "business_failure") for call in arm.operation_runs
    }
    stats = reduce_requests(arm, 0, 0.04)
    assert stats.error_rate == 1 and arm.operation_runs == evidence


async def test_runner_error_measures_actual_elapsed_and_judge_error_is_phase_failure():
    class Bad(Runner):
        async def fire(self, ctx):
            await asyncio.sleep(0.01)
            raise RuntimeError("broken")

    run = await Engine(
        experiment(
            Bad(), LoadPlan(warmup=Warmup(step_s=0), request_rate=1, max_inflight=1, hold_s=0.03)
        )
    ).run()
    assert run.arm_runs[0].operation_runs[0].outcome.duration_ms >= 9

    def broken_judge(o):
        raise ValueError("bad judge")

    run = await Engine(
        experiment(
            BlockingRunner(0.005),
            LoadPlan(warmup=Warmup(step_s=0), request_rate=1, max_inflight=1, hold_s=0.03),
            judge=broken_judge,
        )
    ).run()
    assert not run.passed and run.arm_runs[0].phase_errors
    assert len(run.arm_runs[0].operation_runs) == 1


async def test_generator_stall_does_not_catch_up_after_the_hold_deadline():
    class Stalled(Runner):
        async def fire(self, ctx):
            time.sleep(0.06)  # Deliberately block the generator to exercise missed deadlines.
            return Outcome(status=200, duration_ms=60)

    run = await Engine(
        experiment(
            Stalled(),
            LoadPlan(
                warmup=Warmup(step_s=0),
                request_rate=1000,
                max_inflight=1,
                hold_s=0.04,
            ),
        )
    ).run()
    arm = run.arm_runs[0]
    assert len(arm.requests) == 1
    assert len(arm.operation_runs) == 1
    assert all(r.state == "finished" for r in arm.requests)
    assert arm.measurement.request.n_dropped == 0
    assert arm.measurement.request.completed == 0


async def test_interrupted_run_verdict_and_no_latency_evidence(tmp_path):
    from perf_harness import SloAssertion
    from perf_harness.slo import evaluate_slo
    from perf_harness.verdict import build_verdict_doc

    run = await Engine(
        experiment(
            BlockingRunner(10),
            LoadPlan(
                warmup=Warmup(step_s=0),
                request_rate=float("inf"),
                max_inflight=1,
                hold_s=0.02,
                cooldown_timeout_s=0,
            ),
        )
    ).run()
    assert build_verdict_doc(run)["status"] == "fail"
    checks = evaluate_slo(run.arm_runs[0], [SloAssertion(metric="p99_ms", op="lt", threshold=100)])
    assert checks[0].skipped and checks[0].observed is None


JUDGE_FAILURES = json.loads(
    (Path(__file__).parents[4] / "conformance/perf/fixtures/judge-failure.json").read_text()
)


@pytest.mark.parametrize("scenario", JUDGE_FAILURES, ids=lambda s: s["name"])
async def test_judge_failure_preserves_boundaries_and_census(scenario, tmp_path):
    class RunnerWithSlowCancellation(Runner):
        calls = 0
        active = 0
        cleaned = False

        async def fire(self, ctx):
            self.calls += 1
            first = self.calls == 1
            self.active += 1
            try:
                await asyncio.sleep(scenario["first_response_s"] if first else 10)
            except asyncio.CancelledError:
                await asyncio.sleep(scenario["cancellation_delay_s"])
                raise
            finally:
                self.active -= 1
            return Outcome(
                status=200,
                duration_ms=scenario["first_response_s"] * 1000,
                meta={"trace_id": "completed-before-judge-error"},
            )

        async def cleanup(self, ctx):
            assert self.active == 0
            self.cleaned = True

    def broken_judge(outcome):
        raise ValueError("judge unavailable")

    runner = RunnerWithSlowCancellation()
    run = await Engine(
        experiment(
            runner,
            LoadPlan(
                warmup=Warmup(step_s=0),
                request_rate=float("inf"),
                max_inflight=2,
                hold_s=scenario["duration_s"],
                cooldown_timeout_s=scenario["cooldown_timeout_s"],
            ),
            judge=broken_judge,
        )
    ).run()
    arm = run.arm_runs[0]
    assert not run.passed and runner.cleaned and runner.active == 0
    assert arm.phase_errors[0].message == "judge unavailable"
    assert arm.stop.reason == scenario["stop_reason"]
    assert arm.stop.inflight_at_stop == scenario["inflight_at_stop"]
    assert arm.stop.interrupted == 1 and arm.stop.force_cancelled
    assert arm.measurement.complete == scenario["measurement_complete"]
    assert arm.measurement.end_s > 0
    if scenario["measurement_complete"]:
        assert arm.measurement.end_s == scenario["duration_s"]
    else:
        assert arm.measurement.end_s < scenario["duration_s"]
    assert arm.measurement.request.completed == scenario["completed"]
    assert arm.measurement.request.n == 1
    assert arm.measurement.request.n_interrupted == 1
    assert arm.measurement.request.error_breakdown == {"unjudged": 1}
    assert not arm.evaluations
    finished = next(r for r in arm.requests if r.state == "finished")
    interrupted = next(r for r in arm.requests if r.state == "interrupted")
    assert interrupted.finished_at > arm.measurement.end_s
    drain = next(w for w in arm.windows if w.kind == "cooldown")
    assert drain.start_s == arm.measurement.end_s
    assert drain.request.completed == 1 - scenario["completed"]
    call = next(o for o in arm.operation_runs if o.id == finished.operation_run_id)
    assert call.outcome.meta["trace_id"] == "completed-before-judge-error"
    write_run_data(run, tmp_path)
    restored = load_run(tmp_path).arm_runs[0]
    assert restored.requests == arm.requests and restored.stop == arm.stop
    assert restored.windows == arm.windows
