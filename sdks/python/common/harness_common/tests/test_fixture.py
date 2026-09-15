import asyncio
from dataclasses import dataclass, field

import pytest

from harness_common import (
    Environment,
    EnvironmentBudgets,
    EnvironmentFacts,
    EnvironmentSnapshot,
    run_environment,
)


@dataclass
class State:
    snapshot: EnvironmentSnapshot = field(
        default_factory=lambda: EnvironmentSnapshot("test", "generic")
    )
    events: list[str] = field(default_factory=list)
    client: object = None


class Source:
    key = "fixture-test"

    def __init__(self, state):
        self.state = state

    def create_client(self, clients):
        state = self.state

        class Client:
            closed = False

            async def initialize(self):
                state.events.append("initialize")

            async def dispose(self):
                state.events.append("dispose")
                self.closed = True

        return Client()


@pytest.mark.parametrize("failure", [None, "prepare", "run", "cleanup"])
async def test_environment_owns_order_evidence_and_outer_failures(failure):
    state = State()

    class Fixture:
        async def prepare(self, ctx, state):
            state.events.append("prepare")
            state.client = await ctx.clients.get(Source(state))
            state.snapshot.target = EnvironmentFacts(
                "probe", "now", {"ptrace": "denied"}
            )
            if failure == "prepare":
                raise RuntimeError("partial preparation")

        async def cleanup(self, ctx, state):
            assert ctx.remaining_s > 0
            assert not state.client.closed
            state.events.append("cleanup")
            state.snapshot.target.values["ptrace"] = "allowed"
            if failure == "cleanup":
                raise RuntimeError("cleanup failed")

    async def domain(ctx, state):
        try:
            state.events.append("run")
            if failure == "run":
                raise RuntimeError("domain failed")
            return {"case": "fail"}
        finally:
            state.events.append("case-cleanup")

    result = await run_environment(
        Environment("test"),
        state,
        Fixture(),
        domain,
        budgets=EnvironmentBudgets(1, 1, 1, 1),
        observe=lambda s: s.snapshot,
        required={"ptrace": "denied"},
    )
    assert result.healthy == (failure is None)
    assert state.events == ["prepare", "initialize"] + (
        [] if failure == "prepare" else ["run", "case-cleanup"]
    ) + ["cleanup", "dispose"]
    assert result.result == (
        None if failure in ("prepare", "run") else {"case": "fail"}
    )
    assert [o.snapshot.target.values["ptrace"] for o in result.observations] == [
        "denied",
        "allowed",
    ]
    state.snapshot.target.values.clear()
    assert result.observations[0].snapshot.target.values == {"ptrace": "denied"}


@pytest.mark.parametrize("cancel", [False, True])
async def test_timeout_and_cancellation_finish_domain_then_environment_cleanup(cancel):
    state = State()
    started, cleaning = asyncio.Event(), asyncio.Event()

    class Fixture:
        async def prepare(self, ctx, state):
            state.client = await ctx.clients.get(Source(state))

        async def cleanup(self, ctx, state):
            cleaning.set()
            await asyncio.sleep(0.02)
            assert not state.client.closed
            state.events.append("cleanup")

    async def domain(ctx, state):
        started.set()
        try:
            await asyncio.Event().wait()
        finally:
            state.events.append("case-cleanup")

    task = asyncio.create_task(
        run_environment(
            Environment("test"),
            state,
            Fixture(),
            domain,
            budgets=EnvironmentBudgets(1, 10 if cancel else 0.01, 1, 1),
        )
    )
    await started.wait()
    if cancel:
        task.cancel()
        await cleaning.wait()
        task.cancel()  # repeated cancellation must not leave teardown in the background
        with pytest.raises(asyncio.CancelledError):
            await task
    else:
        result = await task
        assert not result.healthy
        assert "TimeoutError" in result.phases[1].error
    assert state.events == ["initialize", "case-cleanup", "cleanup", "dispose"]


async def test_missing_prerequisite_blocks_domain_but_keeps_evidence_and_cleans():
    state = State()

    class Fixture:
        async def prepare(self, ctx, state):
            state.snapshot.target = EnvironmentFacts("probe", "now", {})

        async def cleanup(self, ctx, state):
            state.events.append("cleanup")

    async def domain(ctx, state):
        pytest.fail("unknown prerequisite must block domain")

    result = await run_environment(
        Environment("test"),
        state,
        Fixture(),
        domain,
        budgets=EnvironmentBudgets(1, 1, 1, 1),
        observe=lambda s: s.snapshot,
        required={"ptrace": "denied"},
    )
    assert not result.healthy and "ptrace" in result.phases[0].error
    assert state.events == ["cleanup"]
    assert len(result.observations) == 2


async def test_cleanup_timeout_and_disposal_error_are_independent_of_domain_result():
    state = State()

    class BrokenSource:
        key = "broken-disposal"

        def create_client(self, clients):
            class Client:
                async def initialize(self):
                    pass

                async def dispose(self):
                    state.events.append("dispose")
                    raise RuntimeError("release failed")

            return Client()

    class Fixture:
        async def prepare(self, ctx, state):
            await ctx.clients.get(BrokenSource())

        async def cleanup(self, ctx, state):
            try:
                await asyncio.Event().wait()
            finally:
                state.events.append("cleanup")

    async def domain(ctx, state):
        return "retained-domain-result"

    result = await run_environment(
        Environment("test"),
        state,
        Fixture(),
        domain,
        budgets=EnvironmentBudgets(1, 1, 0.01, 1),
    )
    assert result.result == "retained-domain-result" and not result.healthy
    assert [p.name for p in result.phases if p.error] == ["cleanup", "dispose"]
    assert state.events == ["cleanup", "dispose"]
