from dataclasses import replace
from unittest.mock import AsyncMock

import pytest

pytest.importorskip("kubernetes_asyncio")

from harness_common import Component, Forge, KubernetesEnvironment, Repository, Service
from harness_common.toolbox import ServiceDataSource, kubernetes_source
from harness_toolbox import ClientManager
from harness_toolbox.kube import KubernetesDataSource, Options
from harness_toolbox.transport import KubernetesAccess, PortForwardTransport

ENV = KubernetesEnvironment("dev", "/config/dev", "cluster-context")
OPTIONS = Options("runtime-ns", 15, 4)
SERVICE = Service(
    "business-name", Component(Repository(Forge("git"), "org/repo"), "server"), ENV
)


def test_reuse_uses_physical_access_not_environment_alias():
    source = kubernetes_source(ENV, OPTIONS)
    assert (
        source.key == kubernetes_source(replace(ENV, name="another-label"), OPTIONS).key
    )
    assert (
        source.key
        != kubernetes_source(replace(ENV, kubeconfig="/other-cluster"), OPTIONS).key
    )
    assert (
        source.key
        != kubernetes_source(replace(ENV, context="other-context"), OPTIONS).key
    )
    assert (
        source.key != kubernetes_source(ENV, replace(OPTIONS, namespace="other-ns")).key
    )


async def test_logical_services_share_access_without_platform_name_assumptions(
    monkeypatch,
):
    kube = AsyncMock()
    kube.access = KubernetesAccess(ENV.kubeconfig, OPTIONS.namespace, ENV.context)
    monkeypatch.setattr(
        KubernetesDataSource, "create_client", lambda source, clients: kube
    )
    source = kubernetes_source(ENV, OPTIONS)
    first = ServiceDataSource(SERVICE, source)
    second = ServiceDataSource(
        replace(SERVICE, name="another-business-service"), source
    )
    async with ClientManager() as clients:
        client = await clients.get(first.source)
        assert client is await clients.get(second.source)
        # A logical Service may have multiple workloads, with entirely different resource names.
        await client.list_deployment_pods("api-workload")
        await client.list_deployment_pods("worker-workload")
        route = PortForwardTransport(client.access, "service/shared-storage", 9200)
        assert route.access.context == ENV.context
        assert route.access.namespace == OPTIONS.namespace
        kube.list_service_pods.assert_not_called()
        kube.initialize.assert_awaited_once()
    kube.dispose.assert_awaited_once()


def test_one_service_can_associate_multiple_independent_sources():
    source = kubernetes_source(ENV, OPTIONS)
    other = kubernetes_source(ENV, replace(OPTIONS, namespace="shared-infra"))
    bindings = [ServiceDataSource(SERVICE, source), ServiceDataSource(SERVICE, other)]
    assert all(binding.service == SERVICE for binding in bindings)
    assert bindings[0].source.key != bindings[1].source.key
