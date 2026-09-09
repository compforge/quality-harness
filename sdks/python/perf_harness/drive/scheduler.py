"""Trial drivers — turn a LoadProfile into actual fires, and stop cleanly.

Two loops (picked by ``load.model``): ``drive_open`` issues arrivals at the
Schedule's time-varying rate λ(t); ``drive_closed`` tracks the Schedule's target
concurrency with virtual user loops. Both share the same stop discipline:
DECIDE (deadline, or the error-rate circuit breaker) → ENACT (stop scheduling →
drain in-flight up to ``graceful_stop_s`` → cancel stragglers), and every trial
ends with a structured ``TrialStop``. A cancelled in-flight request never enters
the latency stats — only completed requests are latency facts.
"""

from __future__ import annotations

import asyncio
import contextlib
import random
import time

from harness_common import OperationRun
from spec_case.model import Case

from perf_harness.drive.load import LoadProfile
from perf_harness.drive.workload import FireContext, TrialContext, Workload
from perf_harness.model import Outcome, StopSnapshot, TrialStop
from perf_harness.observe import ProbeContext


def _pick(cases: list[Case], weights: list[float]) -> Case:
    """Pick one Case by weight (the load mix). Done in the driver so a dropped
    arrival can be attributed to the same Case it would have fired."""
    return random.choices(cases, weights, k=1)[0]  # noqa: S311 — load mix, not crypto


async def _fire(
    workload: Workload,
    trial: TrialContext,
    ctx: ProbeContext,
    case: Case,
    timed: list[tuple[float, Outcome]],
    operation_runs: list[OperationRun[Outcome]] | None = None,
    execution_id: str = "trial",
) -> None:
    # Window attribution follows dispatch time. Completion time would move a long
    # request into a later hold and corrupt both capacity and resource correlation.
    started_at_s = time.monotonic() - ctx.t0
    ctx.stats.start()
    try:
        fire_context = FireContext(trial=trial, case=case)
        outcome = await workload.fire(fire_context)
    except Exception as e:  # noqa: BLE001 — never let one fire kill the generator
        outcome = Outcome(
            status=None,
            duration_ms=0.0,
            meta={"exc": type(e).__name__, "exc_detail": str(e)},
        )
    finally:
        ctx.stats.done()
    # judge is the sole verdict authority — even a transport exception is judged
    # (via meta["exc"]), so ok/error_kind are decided in exactly one place.
    verdict = workload.judge(outcome)
    outcome.ok = verdict.ok
    outcome.error_kind = verdict.error_kind
    outcome.case_id = case.id
    # stamp the fired Case's facets (a Workload may have added runtime-derived ones)
    outcome.facets = {**case.facets, **outcome.facets}
    timed.append((started_at_s, outcome))
    if operation_runs is not None:
        operation_runs.append(
            OperationRun(
                id=f"{execution_id}:{len(operation_runs)}",
                service=trial.service,
                operation=workload.operation(FireContext(trial=trial, case=case)),
                outcome=outcome,
            )
        )


def _breaker_snapshot(
    timed: list[tuple[float, Outcome]], load: LoadProfile, at_s: float
) -> StopSnapshot | None:
    """Mid-trial circuit breaker DECIDE step: a ``StopSnapshot`` once ≥ ``breaker_min_n``
    requests have been SENT and their cumulative error rate (judged failures ÷ sent)
    reaches ``abort_on_error_rate``, else ``None``. Counts the whole run incl. warmup —
    a safety net (stop hammering a failing Service), not a measurement. Drops aren't
    sent; only completed fires are in ``timed`` (in-flight ones haven't appended yet).
    The snapshot is the trip view the report shows — not the post-warmup measurement."""
    threshold = load.abort_on_error_rate
    if threshold is None:
        return None
    sent = [o for (_t, o) in timed if not o.dropped]
    n = len(sent)
    if n < load.breaker_min_n:
        return None
    errors = sum(1 for o in sent if not o.ok)
    rate = errors / n
    if rate < threshold:
        return None
    return StopSnapshot(at_s=at_s, sent=n, errors=errors, error_rate=rate, threshold=threshold)


async def _winddown(
    tasks: list[asyncio.Task], ctx: ProbeContext, graceful_stop_s: float
) -> tuple[int, int, bool]:
    """ENACT step (shared by open + closed, breaker + deadline): bring the load to rest.
    New scheduling is already stopped (the driver loop has exited / set its stop flag);
    here we DRAIN in-flight requests for up to ``graceful_stop_s``, then force-cancel the
    stragglers. Returns ``(inflight_at_stop, interrupted, force_cancelled)`` — the census
    for ``TrialStop``. A cancelled in-flight request never appends an Outcome, so it is a
    census number only, never a latency sample (``ctx.stats.inflight`` is the live
    in-flight *request* count, so the census is model-agnostic)."""
    inflight_at_stop = ctx.stats.inflight
    pending = [t for t in tasks if not t.done()]
    if pending and graceful_stop_s > 0:
        await asyncio.wait(pending, timeout=graceful_stop_s)
    survivors = [t for t in tasks if not t.done()]
    interrupted = ctx.stats.inflight  # still in flight after the drain → about to be cut
    for t in survivors:
        t.cancel()
    if survivors:
        await asyncio.gather(*survivors, return_exceptions=True)
    # force_cancelled tracks cut REQUESTS, not cut tasks: a closed-loop user task in
    # think-time (no in-flight request) is cancelled cleanly — that's not a forced
    # interruption. So tie it to interrupted requests, keeping the census coherent.
    return inflight_at_stop, interrupted, interrupted > 0


async def drive_open(
    workload: Workload,
    trial: TrialContext,
    ctx: ProbeContext,
    cases: list[Case],
    weights: list[float],
    timed: list[tuple[float, Outcome]],
    operation_runs: list[OperationRun[Outcome]],
    execution_id: str,
) -> TrialStop:
    """Open-loop: issue fires at the Schedule's time-varying arrival rate λ(t).

    Driven by integrating λ over *real* elapsed time: each tick adds
    ``λ(t)·dt`` to an accumulator and fires one arrival per whole unit accrued.
    This is drift-free (dt is measured, not assumed), handles a ramp naturally
    (arrivals accelerate as λ climbs, with no infinite first gap when λ≈0), and
    needs no special-casing per shape. Over ``max_inflight`` an arrival is
    recorded as a ``client_saturated`` drop instead of fired.
    """
    load = trial.load
    sched = load.schedule
    deadline = ctx.t0 + sched.total_s
    tick = 0.02
    accum = 0.0
    last = time.monotonic()
    tasks: list[asyncio.Task] = []
    snapshot: StopSnapshot | None = None
    while True:
        now = time.monotonic()
        if now >= deadline:
            break
        snap = _breaker_snapshot(timed, load, now - ctx.t0)  # DECIDE: error-rate breaker
        if snap is not None:
            snapshot = snap  # stop scheduling new arrivals; wind down below
            break
        elapsed = now - ctx.t0
        accum += sched.intensity(elapsed) * (now - last)
        last = now
        while accum >= 1.0:
            accum -= 1.0
            case = _pick(cases, weights)  # pick first so a drop carries the mix's facets
            if load.max_inflight is not None and ctx.stats.inflight >= load.max_inflight:
                # shed load instead of firing: a never-sent request. Recorded as a
                # drop (dropped=True), NOT a 0ms latency sample. max_inflight is a
                # safety rail (OOM guard) — if it engages on real tail events the
                # latency stats understate reality, so the report flags the Trial.
                timed.append(
                    (
                        now - ctx.t0,
                        Outcome(
                            ok=False,
                            status=None,
                            duration_ms=0.0,
                            case_id=case.id,
                            error_kind="client_saturated",
                            dropped=True,
                            facets=dict(case.facets),
                        ),
                    )
                )
            else:
                tasks.append(
                    asyncio.create_task(
                        _fire(
                            workload,
                            trial,
                            ctx,
                            case,
                            timed,
                            operation_runs,
                            execution_id,
                        )
                    )
                )
        await asyncio.sleep(tick)
    # ENACT: drain in-flight up to graceful_stop_s, then cancel; census → TrialStop
    inflight_at_stop, interrupted, forced = await _winddown(tasks, ctx, load.graceful_stop_s)
    return TrialStop(
        reason="error_rate" if snapshot else "deadline",
        snapshot=snapshot,
        inflight_at_stop=inflight_at_stop,
        interrupted=interrupted,
        force_cancelled=forced,
    )


async def drive_closed(
    workload: Workload,
    trial: TrialContext,
    ctx: ProbeContext,
    cases: list[Case],
    weights: list[float],
    timed: list[tuple[float, Outcome]],
    operation_runs: list[OperationRun[Outcome]],
    execution_id: str,
) -> TrialStop:
    """Closed-loop: track the Schedule's target concurrency over time.

    A supervisor checks the target ``round(intensity(t))`` each tick and spawns
    or retires user loops to match — so the same Schedule that ramps an open rate
    also ramps (and, for a spike/step-down, *retires*) concurrent users. Each
    user loops ``fire → wait(pacing)``; retiring cancels the task (a cancelled
    mid-fire still runs ``_fire``'s finally, keeping inflight balanced, and
    appends no outcome). On stop (breaker or deadline) the ``stopping`` event lets
    users finish their current fire, then ``_winddown`` drains/cancels (graceful_stop_s).
    """
    load = trial.load
    sched = load.schedule
    pacing = load.pacing
    deadline = ctx.t0 + sched.total_s
    tick = 0.1

    stopping = asyncio.Event()  # set on stop → users finish current fire then exit (graceful)

    async def user_loop() -> None:
        while not stopping.is_set() and time.monotonic() < deadline:
            fired_at = time.monotonic()
            await _fire(
                workload,
                trial,
                ctx,
                _pick(cases, weights),
                timed,
                operation_runs,
                execution_id,
            )
            wait = pacing.wait_s(time.monotonic() - fired_at)
            if wait > 0:
                # interruptible think-time: wake promptly when stopping is set
                with contextlib.suppress(asyncio.TimeoutError):
                    await asyncio.wait_for(stopping.wait(), timeout=wait)

    users: list[asyncio.Task] = []
    snapshot: StopSnapshot | None = None
    while time.monotonic() < deadline:
        snap = _breaker_snapshot(timed, load, time.monotonic() - ctx.t0)  # DECIDE
        if snap is not None:
            snapshot = snap
            break
        target = max(round(sched.intensity(time.monotonic() - ctx.t0)), 0)
        while len(users) < target:
            users.append(asyncio.create_task(user_loop()))
        while len(users) > target:
            users.pop().cancel()  # retire (normal ramp-down) — not a stop event
        await asyncio.sleep(tick)
    # ENACT: stop scheduling new fires (users finish current fire), then drain + cancel
    stopping.set()
    inflight_at_stop, interrupted, forced = await _winddown(users, ctx, load.graceful_stop_s)
    return TrialStop(
        reason="error_rate" if snapshot else "deadline",
        snapshot=snapshot,
        inflight_at_stop=inflight_at_stop,
        interrupted=interrupted,
        force_cancelled=forced,
    )
