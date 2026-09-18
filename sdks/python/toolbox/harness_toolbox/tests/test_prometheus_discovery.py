import asyncio

import httpx
import pytest
from harness_common import ClientManager, KubernetesWorkload
from prombed import PrombedError

from harness_toolbox.environment import KubernetesEnvironment
from harness_toolbox.kube.environment_resources import KubernetesResourcesClient
from harness_toolbox.prometheus import PrometheusClient, PrometheusDataSource, PrometheusOptions
from harness_toolbox.prometheus_discovery import KubernetesScrapeDiscovery


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
        PrometheusDataSource,
        "create_client",
        lambda source, clients: PrometheusClient(
            source,
            clients=clients,
            transport=httpx.MockTransport(
                lambda request: httpx.Response(200, text=f"active {10 if generation == 1 else 2}\n")
            ),
        ),
    )
    environment = KubernetesEnvironment("dfx", "/config")
    workload = KubernetesWorkload("chat", {"kind": "labels", "labels": {"app": "chat"}}, "ns")
    source = PrometheusDataSource(
        discovery=KubernetesScrapeDiscovery(environment, (workload,), 8080)
    )
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
        PrometheusDataSource,
        "create_client",
        lambda source, clients: PrometheusClient(
            source, clients=clients, transport=httpx.MockTransport(handle)
        ),
    )
    async with ClientManager() as clients:
        client = await clients.get(
            PrometheusDataSource(
                targets=(ScrapeTarget("http://one"), ScrapeTarget("http://two")),
                options=PrometheusOptions(timeout_ms=20, connection_pool_maxsize=1),
            )
        )
        with pytest.raises(PrombedError, match="timeout"):
            await client.read([])
        assert active == 0
