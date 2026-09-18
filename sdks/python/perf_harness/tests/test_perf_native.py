import asyncio
import time

import pytest
from harness_common import Host
from harness_toolbox.environment import KubernetesEnvironment
from harness_toolbox.kube.resource_list import ResourceListClient, ResourceListDataSource

from perf_harness.model import Service
from perf_harness.observe.base import ProbeContext, observe_loop
from perf_harness.observe.k8s import PodCountProbe, ResourceLimitsProbe, RestartProbe


@pytest.mark.parametrize("host", [None, Host("devbox", "ssh", "devbox")])
async def test_native_probes_share_snapshot_refresh_and_dispose(monkeypatch, host):
    stop = asyncio.Event()
    calls = []
    readers = []

    class Reader(ResourceListClient):
        disposed = False

        async def initialize(self):
            pass

        async def dispose(self):
            self.disposed = True

        async def _list(self, api_version, kind, *, label_selector):
            calls.append((api_version, kind, label_selector))
            count = len(calls)
            if count == 2:
                stop.set()
            return {
                "items": [
                    {
                        "metadata": {"name": f"chat-{n}"},
                        "status": {"phase": "Running", "containerStatuses": [{"restartCount": 1}]},
                        "spec": {"containers": [{"resources": {"limits": {"cpu": "1"}}}]},
                    }
                    for n in range(count)
                ]
            }

    def create(source, clients):
        assert source.environment.host == host
        assert source.environment.context == "chosen"
        assert source.options.namespace == "ns"
        readers.append(Reader(source, clients))
        return readers[-1]

    monkeypatch.setattr(ResourceListDataSource, "create_client", create)
    service = Service(
        name="chat",
        environment=KubernetesEnvironment("dev", "/config", "chosen", host=host),
        namespace="ns",
        k8s_selector="app=chat",
    )
    probes = [p(target_service=service) for p in [RestartProbe, ResourceLimitsProbe, PodCountProbe]]
    ctx = ProbeContext(service=service, client=None, t0=time.monotonic())
    store = {}
    async with ctx.clients:
        failures = await observe_loop(probes, ctx, store, stop, 0.001)
    assert failures == {}
    assert calls == [("v1", "Pod", "app=chat")] * 2
    assert len(readers) == 1 and readers[0].disposed
    assert [s.value for s in store[("restart.chat", "restarts")]] == [1, 2]
    assert [s.value for s in store[("limits.chat", "cpu_limit")]] == [1000, 2000]
    assert [s.value for s in store[("pods.chat", 'count{state="total"}')]] == [1, 2]


async def test_native_error_is_probe_failure_and_cancel_closes_clients(monkeypatch):
    started = asyncio.Event()
    disposed = asyncio.Event()

    class Reader(ResourceListClient):
        async def initialize(self):
            pass

        async def dispose(self):
            disposed.set()

        async def _list(self, *args, **kwargs):
            started.set()
            raise RuntimeError("403 denied")

    monkeypatch.setattr(
        ResourceListDataSource, "create_client", lambda source, clients: Reader(source, clients)
    )
    service = Service(
        environment=KubernetesEnvironment("dev", "/config"), namespace="ns", k8s_selector="app=chat"
    )
    ctx = ProbeContext(service=service, client=None, t0=time.monotonic())
    store = {}
    task = asyncio.create_task(observe_loop([RestartProbe()], ctx, store, asyncio.Event(), 60))
    await started.wait()
    while not store:
        await asyncio.sleep(0)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert not disposed.is_set()  # window queries still borrow this client
    await ctx.clients.dispose()
    assert disposed.is_set()
    assert store[("restart", "up")][0].value == 0
    assert ("restart", "restarts") not in store


async def test_direct_samples_refresh_without_observer_tick(monkeypatch):
    class Reader(ResourceListClient):
        reads = 0

        async def initialize(self):
            pass

        async def dispose(self):
            pass

        async def _list(self, *args, **kwargs):
            self.reads += 1
            return {"items": [{"status": {"containerStatuses": [{"restartCount": self.reads}]}}]}

    monkeypatch.setattr(
        ResourceListDataSource, "create_client", lambda source, clients: Reader(source, clients)
    )
    service = Service(
        environment=KubernetesEnvironment("dev", "/config"), namespace="ns", k8s_selector="app=chat"
    )
    ctx = ProbeContext(service=service, client=None, t0=time.monotonic())
    async with ctx.clients:
        assert await RestartProbe().sample(ctx) == {"restarts": 1}
        assert await RestartProbe().sample(ctx) == {"restarts": 2}
