"""One bounded scheduler for finite arrivals and concurrency-driven replenishment."""

from __future__ import annotations

import asyncio
import random
import time
from contextlib import suppress
from dataclasses import dataclass, field, replace

from harness_common import Operation, OperationRun
from spec_case.model import Case

from perf_harness.drive.controller import LoadController
from perf_harness.drive.runner import ArmContext, FireContext, Runner
from perf_harness.judge import Judge
from perf_harness.model import ArmRun, ArmStop, Outcome, StopSnapshot, Window
from perf_harness.observe.base import ProbeContext
from perf_harness.records import RequestRecord


@dataclass
class DriveState:
    """ArmRun-owned facts that survive scheduler and Judge exceptions."""

    measurement_end_s: float | None = None
    measurement_start_s: float = 0.0
    windows: list[Window] = field(default_factory=list)
    phase: str = "warmup"
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
    controller = LoadController(load)
    state.windows = controller.windows
    due = 0.0
    last_dispatch: float | None = None
    last_rate: float | None = None
    was_full = False
    reason, snapshot = "deadline", None

    def now() -> float:
        return time.monotonic() - ctx.t0

    async def fire(record: RequestRecord, case: Case) -> None:
        nonlocal completed, errors
        record.dispatched_at = now()
        record.state = "dispatched"
        record.operation_run_id = record.id
        controller.advance(record.dispatched_at, ctx.stats.inflight)
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
        try:
            evaluation = judge(outcome)
        except BaseException as error:
            # Publish before yielding: a just-freed slot must not refill after Judge failure.
            failures.append(error)
            raise
        execution.evaluations[record.id] = evaluation
        completed += 1
        errors += not evaluation.ok

    def settled(task: asyncio.Task) -> None:
        active.discard(task)
        if (
            not task.cancelled()
            and task.exception() is not None
            and task.exception() not in failures
        ):
            failures.append(task.exception())
        wake.set()

    def offer(scheduled: float) -> bool:
        at_s = now()
        if at_s >= controller.deadline or ctx.stats.inflight >= controller.target(at_s)[1]:
            return False
        case = rng.choices(cases, weights=weights, k=1)[0]
        record = RequestRecord(
            id=f"{execution.id}:{len(execution.requests)}",
            case_id=case.id,
            scheduled_at=scheduled,
            arrived_at=now(),
            facets=dict(case.facets),
        )
        execution.requests.append(record)
        # Reserve before creating a Task: a batch of due arrivals must see previous reservations.
        ctx.stats.start()
        task = asyncio.create_task(fire(record, case))
        active.add(task)
        task.add_done_callback(settled)
        return True

    try:
        while True:
            elapsed = now()
            controller.advance(
                elapsed,
                ctx.stats.inflight,
                limited=ctx.stats.inflight >= controller.target(elapsed)[1] and elapsed >= due,
            )
            state.phase = controller.phase
            if failures:
                raise failures[0]
            if controller.done:
                break
            if (
                load.abort_on_error_rate is not None
                and completed >= load.breaker_min_n
                and errors / completed >= load.abort_on_error_rate
            ):
                reason = "error_rate"
                snapshot = StopSnapshot(
                    elapsed, completed, errors, errors / completed, load.abort_on_error_rate
                )
                controller.abort(elapsed)
                break
            rate, cap = controller.target(elapsed)
            if rate != last_rate:
                due = (
                    elapsed
                    if last_dispatch is None
                    else max(elapsed, last_dispatch + (1 / rate if rate else float("inf")))
                )
                last_rate = rate
            if was_full and ctx.stats.inflight < cap:
                due = max(due, elapsed)
            was_full = ctx.stats.inflight >= cap
            wake.clear()
            if ctx.stats.inflight < cap and rate and due <= elapsed:
                # why: no queued arrivals while full, and no catch-up burst after a stall.
                if not offer(due):
                    continue
                last_dispatch = elapsed
                gap = arrival_rng.expovariate(rate) if load.arrival == "poisson" else 1 / rate
                due = elapsed + gap
                await asyncio.sleep(0)
                continue
            delay = min(0.02, max(0, controller.deadline - now()))
            # Wake at the next pacing opportunity even while full, so limited time
            # starts when rate would allow a send, not when the request was admitted.
            if rate and due > now():
                delay = min(delay, due - now())
            with suppress(TimeoutError):
                await asyncio.wait_for(wake.wait(), timeout=delay)
        state.measurement_end_s = controller.end_s
        state.measurement_start_s = (
            controller.hold_start_s
            if controller.hold_start_s is not None
            else state.measurement_end_s
        )
        state.stop = ArmStop(reason=reason, snapshot=snapshot, inflight_at_stop=ctx.stats.inflight)
        state.phase = "cooldown"
        if active:
            await asyncio.wait(active, timeout=load.cooldown_timeout_s)
    finally:
        # Freeze load facts BEFORE waiting for cancellation; cleanup cannot rewrite them.
        if state.measurement_end_s is None:
            controller.abort(now())
            state.measurement_end_s = controller.end_s
            state.measurement_start_s = (
                controller.hold_start_s if controller.hold_start_s is not None else controller.end_s
            )
            state.stop = ArmStop(reason="aborted", inflight_at_stop=ctx.stats.inflight)
        remaining = list(active)
        for task in remaining:
            task.cancel()
        if remaining:
            await asyncio.gather(*remaining, return_exceptions=True)
        interrupted = sum(r.state == "interrupted" for r in execution.requests)
        state.stop = replace(state.stop, interrupted=interrupted, force_cancelled=bool(interrupted))
        state.windows.append(
            Window(
                id="cooldown",
                name="cooldown",
                kind="cooldown",
                start_s=state.measurement_end_s,
                end_s=now(),
                complete=not bool(interrupted),
                end_reason=(
                    "cancelled"
                    if interrupted and state.stop.reason == "aborted"
                    else "timeout"
                    if interrupted
                    else "empty"
                ),
            )
        )
    if failures:
        raise failures[0]
