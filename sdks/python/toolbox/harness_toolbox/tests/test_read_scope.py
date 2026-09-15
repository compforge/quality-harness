import asyncio

import pytest

from harness_toolbox.read_scope import ReadScope


async def test_scope_shares_reads_and_errors_then_refreshes():
    calls = 0

    async def read():
        nonlocal calls
        calls += 1
        await asyncio.sleep(0)
        return {"value": calls}

    async with ReadScope() as scope:
        a, b = await asyncio.gather(scope.read("a", read), scope.read("a", read))
        assert a is b and calls == 1
        assert (await scope.read("b", read))["value"] == 2
    with pytest.raises(RuntimeError, match="not active"):
        await scope.read("a", read)
    async with ReadScope() as scope:
        assert (await scope.read("a", read))["value"] == 3

    error = ValueError("failed query")
    failures = 0

    async def fail():
        nonlocal failures
        failures += 1
        raise error

    async with ReadScope() as scope:
        for _ in range(2):
            with pytest.raises(ValueError) as caught:
                await scope.read("failure", fail)
            assert caught.value is error
    assert failures == 1
    async with ReadScope() as scope:
        with pytest.raises(ValueError):
            await scope.read("failure", fail)
    assert failures == 2


async def test_cancelled_waiter_does_not_cancel_shared_read():
    started, release = asyncio.Event(), asyncio.Event()

    async def read():
        started.set()
        await release.wait()
        return 42

    async with ReadScope() as scope:
        waiter = asyncio.create_task(scope.read("query", read))
        await started.wait()
        waiter.cancel()
        with pytest.raises(asyncio.CancelledError):
            await waiter
        release.set()
        assert await scope.read("query", read) == 42


async def test_scope_exit_joins_unfinished_read_and_discards_results():
    started, finished = asyncio.Event(), asyncio.Event()

    async def read():
        started.set()
        try:
            await asyncio.Event().wait()
        finally:
            finished.set()

    async with ReadScope() as scope:
        waiter = asyncio.create_task(scope.read("query", read))
        await started.wait()
    with pytest.raises(asyncio.CancelledError):
        await waiter
    assert finished.is_set()
    assert not scope._reads
    await scope.aclose()


async def test_scope_caches_synchronous_loader_failure():
    calls = 0

    def fail():
        nonlocal calls
        calls += 1
        raise ValueError("cannot create read")

    async with ReadScope() as scope:
        for _ in range(2):
            with pytest.raises(ValueError):
                await scope.read("query", fail)
    assert calls == 1
