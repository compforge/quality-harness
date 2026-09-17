"""One bounded scheduler for finite arrivals and concurrency-driven replenishment."""

from __future__ import annotations

import asyncio
import random
import time
from contextlib import suppress
from dataclasses import dataclass, field, replace

from harness_common import Operation, OperationRun
from spec_case.model import Case

from perf_harness.drive.runner import ArmContext, FireContext, Runner
from perf_harness.judge import Judge
from perf_harness.model import ArmRun, ArmStop, Outcome, StopSnapshot
from perf_harness.observe.base import ProbeContext
from perf_harness.records import RequestRecord


@dataclass
class DriveState:
    """ArmRun-owned facts that survive scheduler and Judge exceptions."""

    measurement_end_s: float | None = None
    stop: ArmStop = field(default_factory=lambda: ArmStop(reason="aborted"))


async def drive(
    runner: Runner,
    judge: Judge,
    context: ArmContext,
    ctx: ProbeContext,
    cases: list[Case],
    weights: list[float],
    execution: ArmRun,
    state: DriveState,
) -> None:
    load = context.load
    rng = random.Random(load.seed)
    arrival_rng = random.Random(load.seed)
    active: set[asyncio.Task] = set()
    wake = asyncio.Event()
    failures: list[BaseException] = []
    completed = errors = 0
    volume = 0.0
    due = 0.0 if load.saturated else load.arrival_time(volume)
    reason, snapshot = "deadline", None

    def now() -> float:
        return time.monotonic() - ctx.t0

    async def fire(record: RequestRecord, case: Case) -> None:
        nonlocal completed, errors
        record.dispatched_at = now()
        record.state = "dispatched"
        record.operation_run_id = record.id
        operation = Operation(name=runner.name)
        try:
            operation = runner.operation(FireContext(context, case))
            outcome = await runner.fire(FireContext(context, case))
        except asyncio.CancelledError:
            record.state = "interrupted"
            record.reason = "cancelled"
            outcome = Outcome(
                status=None,
                duration_ms=(now() - record.dispatched_at) * 1000,
                meta={"interrupted": True},
                case_id=case.id,
                facets=dict(case.facets),
            )
            raise
        except Exception as error:
            outcome = Outcome(
                status=None,
                duration_ms=(now() - record.dispatched_at) * 1000,
                meta={"exc": type(error).__name__, "exc_detail": str(error)},
            )
        finally:
            record.finished_at = now()
            ctx.stats.done()
            # Even cancellation has an actual-call record, but never a fabricated response.
            if record.state == "interrupted":
                execution.operation_runs.append(
                    OperationRun(
                        id=record.id, service=context.service, operation=operation, outcome=outcome
                    )
                )
        record.state = "finished"
        outcome.case_id = case.id
        outcome.facets = {**case.facets, **outcome.facets}
        record.facets = dict(outcome.facets)
        execution.operation_runs.append(
            OperationRun(
                id=record.id, service=context.service, operation=operation, outcome=outcome
            )
        )
        evaluation = judge(outcome)
        execution.evaluations[record.id] = evaluation
        completed += 1
        errors += not evaluation.ok

    def settled(task: asyncio.Task) -> None:
        active.discard(task)
        if not task.cancelled() and task.exception() is not None:
            failures.append(task.exception())
        wake.set()

    def offer(scheduled: float, drop_reason: str | None = None) -> None:
        case = rng.choices(cases, weights=weights, k=1)[0]
        record = RequestRecord(
            id=f"{execution.id}:{len(execution.requests)}",
            case_id=case.id,
            scheduled_at=scheduled,
            arrived_at=now(),
            facets=dict(case.facets),
        )
        execution.requests.append(record)
        _, cap = load.target(record.arrived_at)
        if drop_reason or ctx.stats.inflight >= cap:
            record.state, record.reason, record.finished_at = (
                "dropped",
                drop_reason or "concurrency_limit",
                record.arrived_at,
            )
            ctx.stats.dropped += 1
            return
        # Reserve before creating a Task: a batch of due arrivals must see previous reservations.
        ctx.stats.start()
        task = asyncio.create_task(fire(record, case))
        active.add(task)
        task.add_done_callback(settled)

    try:
        while now() < load.duration_s:
            if failures:
                raise failures[0]
            if (
                load.abort_on_error_rate is not None
                and completed >= load.breaker_min_n
                and errors / completed >= load.abort_on_error_rate
            ):
                reason = "error_rate"
                snapshot = StopSnapshot(
                    now(), completed, errors, errors / completed, load.abort_on_error_rate
                )
                break
            elapsed = now()
            wake.clear()
            if load.saturated:
                _, cap = load.target(elapsed)
                for _ in range(max(0, cap - ctx.stats.inflight)):
                    offer(elapsed)
            else:
                # Yield per offer so cancellation progresses even after a stall.
                if due <= elapsed:
                    offer(due)
                    volume += arrival_rng.expovariate(1) if load.arrival == "poisson" else 1
                    due = load.arrival_time(volume)
                    await asyncio.sleep(0)
                    continue
            delay = min(0.02, max(0, load.duration_s - now()))
            if not load.saturated:
                delay = min(delay, max(0, due - now()))
            with suppress(TimeoutError):
                await asyncio.wait_for(wake.wait(), timeout=delay)
        # Account for planned arrivals missed by a stalled generator; never send after deadline.
        if reason == "deadline" and not load.saturated:
            while due < load.duration_s:
                offer(due, "scheduler_deadline")
                volume += arrival_rng.expovariate(1) if load.arrival == "poisson" else 1
                due = load.arrival_time(volume)
                await asyncio.sleep(0)
        state.measurement_end_s = load.duration_s if reason == "deadline" else snapshot.at_s
        state.stop = ArmStop(reason=reason, snapshot=snapshot, inflight_at_stop=ctx.stats.inflight)
        if active:
            await asyncio.wait(active, timeout=load.drain_timeout_s)
    finally:
        # Freeze the boundary before cancellation/join; a failing Judge in drain must
        # neither extend measurement nor erase the completed calls and stop census.
        if state.measurement_end_s is None:
            state.measurement_end_s = min(now(), load.duration_s)
            state.stop = ArmStop(reason="aborted", inflight_at_stop=ctx.stats.inflight)
        # Join cancellations before returning ownership of clients or environment resources.
        remaining = list(active)
        for task in remaining:
            task.cancel()
        if remaining:
            await asyncio.gather(*remaining, return_exceptions=True)
        interrupted = sum(r.state == "interrupted" for r in execution.requests)
        state.stop = replace(state.stop, interrupted=interrupted, force_cancelled=bool(interrupted))
    if failures:
        raise failures[0]
