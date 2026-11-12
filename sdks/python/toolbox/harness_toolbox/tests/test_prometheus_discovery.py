import asyncio

import httpx
import pytest
from harness_common import ClientManager, KubernetesWorkload
from prombed import PrombedError

from harness_toolbox.environment import KubernetesEnvironment
from harness_toolbox.kube.environment_resources import KubernetesResourcesClient
from harness_toolbox.metric import MetricClient, MetricDataSource, MetricOptions


async def test_discovery_reuses_access_tracks_replacement_uid_and_retires_old_gauge(monkeypatch):
    now = 1000000
    generation = 1
    monkeypatch.setattr("prombed.prombed._now_ms", lambda: now)
    accesses = []

    def pod():
        return {
            "metadata": {"name": "chat-0", "namespace": "ns", "uid": f"uid-{generation}"},
            "status": {"phase": "Running", "podIP": "10.0.0.1"},
        }

    class Resources(KubernetesResourcesClient):
        async def initialize(self):
            accesses.append(self._source)

        async def list(self, api_version, kind, *, label_selector=""):
            assert label_selector == "app=chat"
            return {"items": [pod()]}

        async def get(self, api_version, kind, name):
            return pod()

    monkeypatch.setattr(
        KubernetesEnvironment, "create_client", lambda source, clients: Resources(source, clients)
    )
    monkeypatch.setattr(
        MetricDataSource,
        "create_client",
        lambda source, clients: MetricClient(
            source,
            clients=clients,
            transport=httpx.MockTransport(
                lambda request: httpx.Response(200, text=f"active {10 if generation == 1 else 2}\n")
            ),
        ),
    )
    environment = KubernetesEnvironment("dfx", "/config")
    workload = KubernetesWorkload("chat", {"kind": "labels", "labels": {"app": "chat"}}, "ns")
    source = MetricDataSource(environment=environment, workloads=(workload,), port=8080)
    async with ClientManager() as clients:
        client = await clients.get(source)
        first = (await client.read(["active"]))["active"]["result"]
        assert first[0]["metric"]["instance"] == "ns/uid-1"
        now += 1000
        generation = 2
        second = await client.read(["active", "sum(active)"])
        assert len(second["active"]["result"]) == 1
        assert second["active"]["result"][0]["metric"]["instance"] == "ns/uid-2"
        assert float(second["sum(active)"]["result"][0]["value"][1]) == 2
    assert len(accesses) == 1 and accesses[0].options.namespace == "ns"


async def test_whole_fleet_timeout_joins_inflight_fetches(monkeypatch):
    from prombed import ScrapeTarget

    active = 0

    async def handle(request):
        nonlocal active
        active += 1
        try:
            await asyncio.Event().wait()
        finally:
            active -= 1

    monkeypatch.setattr(
        MetricDataSource,
        "create_client",
        lambda source, clients: MetricClient(
            source, clients=clients, transport=httpx.MockTransport(handle)
        ),
    )
    async with ClientManager() as clients:
        client = await clients.get(
            MetricDataSource(
                targets=(ScrapeTarget("http://one"), ScrapeTarget("http://two")),
                options=MetricOptions(timeout_ms=20, connection_pool_maxsize=1),
            )
        )
        with pytest.raises(PrombedError, match="timeout"):
            await client.read([])
        assert active == 0


async def test_workload_port_forward_reuses_uid_route_and_root_closes_it(monkeypatch):
    from contextlib import asynccontextmanager

    from harness_toolbox.http import HTTPClient, HTTPClientProvider
    from harness_toolbox.kube_portforward import KubernetesPortForwardTransport
    from harness_toolbox.transport import Endpoint

    events = []
    generation = 1
    now = 1_000_000
    monkeypatch.setattr("prombed.prombed._now_ms", lambda: now)

    class Resources(KubernetesResourcesClient):
        async def initialize(self):
            pass

        async def list(self, api_version, kind, *, label_selector=""):
            return {
                "items": [
                    {
                        "metadata": {"name": "chat", "namespace": "ns", "uid": f"uid-{generation}"},
                        "status": {"phase": "Running", "podIP": "10.0.0.1"},
                    }
                ]
            }

        async def get(self, api_version, kind, name):
            return (await self.list(api_version, kind))["items"][0]

    class Route(KubernetesPortForwardTransport):
        def create_client(self, clients):
            return self

        async def initialize(self):
            events.append("route-open")

        async def dispose(self):
            events.append("route-close")

        async def pod_endpoint(self, name, uid, port):
            events.append(uid)
            return Endpoint("10.0.0.1", port)

        @asynccontextmanager
        async def connect(self, target):
            yield target

    pools = []

    def pool(provider, clients):
        pools.append(provider.pool)
        return HTTPClient(
            transport=httpx.MockTransport(lambda _: httpx.Response(200, text="active 1\n"))
        )

    monkeypatch.setattr(
        KubernetesEnvironment, "create_client", lambda source, clients: Resources(source, clients)
    )
    monkeypatch.setattr("harness_toolbox.metric.KubernetesPortForwardTransport", Route)
    monkeypatch.setattr(HTTPClientProvider, "create_client", pool)
    source = MetricDataSource(
        environment=KubernetesEnvironment("qa", "/config"),
        workloads=(
            KubernetesWorkload("chat", {"kind": "labels", "labels": {"app": "chat"}}, "ns"),
        ),
        port=8001,
        transport="port-forward",
    )
    async with ClientManager() as owner:
        client = await owner.get(source)
        await client.read(["active"])
        now += 1000
        await client.read(["active"])
        generation = 2
        now += 1000
        await client.read(["active"])
        assert len(pools) == 2 and pools[0] != pools[1]
        assert events.count("route-open") == 1
        assert list(client.diagnostics.values())[0]["transport"] == "port-forward"
    assert events[-1] == "route-close"
