"""Environment lifetime around a domain-owned execution (no Case abstraction)."""

from __future__ import annotations

import asyncio
import time
from collections.abc import Awaitable, Callable
from copy import deepcopy
from dataclasses import dataclass, field
from typing import Generic, Protocol, TypeVar

from harness_common.client import ClientManager
from harness_common.context import EnvironmentContext
from harness_common.environment import Environment, EnvironmentSnapshot

S = TypeVar("S")
S_contra = TypeVar("S_contra", contravariant=True)
R = TypeVar("R")


@dataclass(frozen=True)
class FixtureContext(EnvironmentContext):
    """Fixture phase information extends the neutral environment context."""

    phase: str


class EnvironmentFixture(Protocol[S_contra]):
    """Project-owned shared resources; retain partial handles in preallocated state.

    Cleanup must tolerate incomplete preparation. Borrowed clients remain valid
    until cleanup returns; only their manager owns final disposal.
    """

    async def prepare(self, ctx: FixtureContext, state: S_contra) -> None: ...
    async def cleanup(self, ctx: FixtureContext, state: S_contra) -> None: ...


@dataclass(frozen=True)
class EnvironmentBudgets:
    prepare_s: float
    run_s: float
    cleanup_s: float
    dispose_s: float


@dataclass(frozen=True)
class EnvironmentPhase:
    name: str
    duration_ms: int
    error: str | None = None


@dataclass(frozen=True)
class EnvironmentObservation:
    phase: str
    snapshot: EnvironmentSnapshot


@dataclass
class EnvironmentRun(Generic[R]):
    """Outer health and evidence; the domain result retains its own verdicts."""

    result: R | None = None
    phases: list[EnvironmentPhase] = field(default_factory=list)
    observations: list[EnvironmentObservation] = field(default_factory=list)

    @property
    def healthy(self) -> bool:
        return all(phase.error is None for phase in self.phases)


async def run_environment(
    environment: Environment,
    state: S,
    fixture: EnvironmentFixture[S],
    run: Callable[[EnvironmentContext, S], Awaitable[R]],
    *,
    budgets: EnvironmentBudgets,
    observe: Callable[[S], EnvironmentSnapshot] | None = None,
    required: dict[str, str] | None = None,
) -> EnvironmentRun[R]:
    """Prepare → domain (including case cleanup) → environment cleanup → dispose.

    Exceptions become outer phase errors without inventing a domain result.
    Cancellation propagates after bounded teardown. Async callbacks must cooperate
    with cancellation and finish their own child work before returning.
    Disposal has a cooperative deadline: native cleanup is joined even on overrun.
    ``observe`` is a synchronous view of facts already collected by the project.
    """
    if any(
        value <= 0
        for value in (
            budgets.prepare_s,
            budgets.run_s,
            budgets.cleanup_s,
            budgets.dispose_s,
        )
    ):
        raise ValueError("environment phases require positive budgets")
    if required and observe is None:
        raise ValueError("required facts need an observe callback")
    clients = ClientManager()
    execution: EnvironmentRun[R] = EnvironmentRun()

    def capture(phase: str) -> EnvironmentSnapshot | None:
        if observe is None:
            return None
        snapshot = deepcopy(observe(state))
        execution.observations.append(EnvironmentObservation(phase, snapshot))
        return snapshot

    async def phase(
        name: str,
        budget: float,
        callback: Callable[[FixtureContext], Awaitable[None]],
        *,
        cooperative: bool = False,
    ) -> bool:
        started = time.monotonic()
        error = None
        try:
            ctx = FixtureContext(
                environment, clients, deadline=started + budget, phase=name
            )
            if cooperative:
                await callback(ctx)
                if ctx.remaining_s <= 0:
                    raise TimeoutError(f"{name} exceeded its time budget")
            else:
                async with asyncio.timeout(budget):
                    await callback(ctx)
        except Exception as exc:
            error = f"{type(exc).__name__}: {exc}"
        finally:
            execution.phases.append(
                EnvironmentPhase(name, int((time.monotonic() - started) * 1000), error)
            )
        return error is None

    async def prepare(ctx: FixtureContext) -> None:
        try:
            await fixture.prepare(ctx, state)
        finally:
            # Capture partial preparation too, before any teardown can rewrite it.
            snapshot = capture("prepare")
        if snapshot is not None:
            if (snapshot.name, snapshot.kind) != (environment.name, environment.kind):
                raise ValueError("snapshot conflicts with selected environment")
            if required and (
                not snapshot.target.source or not snapshot.target.observed_at
            ):
                raise ValueError("required target facts need source and observed_at")
            for key, expected in (required or {}).items():
                if snapshot.target.values.get(key) != expected:
                    raise ValueError(
                        f"environment condition {key}: observed {snapshot.target.values.get(key)!r}, required {expected!r}"
                    )

    async def execute(ctx: EnvironmentContext) -> None:
        execution.result = await run(ctx, state)

    async def cleanup(ctx: FixtureContext) -> None:
        try:
            await fixture.cleanup(ctx, state)
        finally:
            capture("cleanup")

    async def teardown() -> None:
        try:
            await phase("cleanup", budgets.cleanup_s, cleanup)
        finally:
            # ClientManager shields native disposal. Join it before returning;
            # report overruns instead of leaking disposal into the next execution.
            await phase(
                "dispose",
                budgets.dispose_s,
                lambda ctx: clients.dispose(),
                cooperative=True,
            )

    try:
        if await phase("prepare", budgets.prepare_s, prepare):
            await phase("run", budgets.run_s, execute)
    finally:
        # Teardown owns a separate task so cancellation of the run cannot skip
        # cleanup or invalidate borrowed clients while cleanup still uses them.
        task = asyncio.create_task(teardown())
        interrupted = False
        while not task.done():
            try:
                await asyncio.shield(task)
            except asyncio.CancelledError:
                interrupted = True
        task.result()
        if interrupted:
            raise asyncio.CancelledError
    return execution
