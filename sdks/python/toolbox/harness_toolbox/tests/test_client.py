import asyncio
from dataclasses import dataclass

import pytest

from harness_toolbox import ClientManager, data_source_key


class Probe:
    def __init__(self, events, *, gate=None, fail=False, dependency=None, provider=None):
        self.events, self.gate, self.fail = events, gate, fail
        self.dependency, self.provider = dependency, provider
        self.closed = False

    async def initialize(self):
        self.events.append("start")
        if self.dependency:
            await self.provider.get(self.dependency)
        if self.gate:
            await self.gate.wait()
        if self.fail:
            raise ValueError("initialization failed")
        self.events.append("ready")

    async def dispose(self):
        if not self.closed:
            self.closed = True
            self.events.append("close")


@dataclass
class Source:
    key: str
    factory: object

    def create_client(self, clients):
        return self.factory(clients)


async def test_concurrent_consumers_and_cancelled_waiter_share_initialization():
    events, gate = [], asyncio.Event()
    source = Source("db", lambda _: Probe(events, gate=gate))
    async with ClientManager() as clients:
        first = asyncio.create_task(clients.get(source))
        second = asyncio.create_task(clients.get(source))
        await asyncio.sleep(0)
        await asyncio.sleep(0)
        first.cancel()
        with pytest.raises(asyncio.CancelledError):
            await first
        gate.set()
        client = await second
        assert client is await clients.get(source)
    assert events == ["start", "ready", "close"]


async def test_failed_initialization_is_cleaned_before_retry():
    events = []
    attempts = 0

    def create(_):
        nonlocal attempts
        attempts += 1
        return Probe(events, fail=attempts == 1)

    source = Source("db", create)
    async with ClientManager() as clients:
        with pytest.raises(ValueError):
            await clients.get(source)
        assert events == ["start", "close"]
        await clients.get(source)
    assert events == ["start", "close", "start", "ready", "close"]


async def test_root_disposal_cancels_initialization_and_is_idempotent():
    events, gate = [], asyncio.Event()
    manager = ClientManager()
    waiter = asyncio.create_task(manager.get(Source("db", lambda _: Probe(events, gate=gate))))
    while not events:
        await asyncio.sleep(0)
    await asyncio.gather(manager.dispose(), manager.dispose())
    with pytest.raises(asyncio.CancelledError):
        await waiter
    assert events == ["start", "close"]
    with pytest.raises(RuntimeError, match="disposed"):
        await manager.get(Source("new", lambda _: Probe(events)))


async def test_dependencies_close_after_consumers_even_on_cleanup_error():
    order = []

    class NamedProbe(Probe):
        async def dispose(self):
            order.append(self.events)
            if self.events == "consumer":
                raise ValueError("cleanup failed")

        async def initialize(self):
            if self.dependency:
                await self.provider.get(self.dependency)

    dependency = Source("dependency", lambda _: NamedProbe("dependency"))
    consumer = Source(
        "consumer",
        lambda provider: NamedProbe("consumer", dependency=dependency, provider=provider),
    )
    manager = ClientManager()
    await manager.get(consumer)
    with pytest.raises(ExceptionGroup):
        await manager.dispose()
    assert order == ["consumer", "dependency"]


def test_key_canonicalizes_configuration_and_hides_credentials():
    assert data_source_key("db", {"host": "a", "password": "secret"}) == data_source_key(
        "db", {"password": "secret", "host": "a"}
    )
    assert "secret" not in data_source_key("db", {"password": "secret"})
    assert data_source_key("db", {"password": "a"}) != data_source_key("db", {"password": "b"})
