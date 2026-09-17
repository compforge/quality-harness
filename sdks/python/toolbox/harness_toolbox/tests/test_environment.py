import time
from dataclasses import replace
from unittest.mock import AsyncMock

import pytest

pytest.importorskip("kubernetes_asyncio")

from harness_common import (
    Component,
    Environment,
    EnvironmentContext,
    Forge,
    Repository,
    Service,
    ServiceDataSource,
)

from harness_toolbox import ClientManager
from harness_toolbox.environment import KubernetesEnvironment
from harness_toolbox.kube import Options
from harness_toolbox.kube.resource_list import ResourceListDataSource
from harness_toolbox.transport import KubernetesAccess, PortForwardTransport

OPTIONS = Options("runtime-ns", 15, 4)
ENV = KubernetesEnvironment("dev", "/config/dev", "cluster-context", options=OPTIONS)
SERVICE = Service("business-name", Component(Repository(Forge("git"), "org/repo"), "server"), ENV)


def test_reuse_uses_physical_access_not_environment_alias():
    source = ENV
    assert source.client_key == replace(ENV, name="another-label").client_key
    assert source.client_key != replace(ENV, kubeconfig="/other-cluster").client_key
    assert source.client_key != replace(ENV, context="other-context").client_key
    assert (
        source.client_key != replace(ENV, options=replace(OPTIONS, namespace="other-ns")).client_key
    )


async def test_logical_services_share_access_without_platform_name_assumptions(
    monkeypatch,
):
    kube = AsyncMock()
    kube.access = KubernetesAccess(ENV.kubeconfig, OPTIONS.namespace, ENV.context)
    monkeypatch.setattr(KubernetesEnvironment, "create_client", lambda source, clients: kube)
    async with ClientManager() as clients:
        first = EnvironmentContext(SERVICE.environment, clients, time.monotonic() + 15)
        other_service = replace(SERVICE, name="another-business-service")
        second = EnvironmentContext(other_service.environment, clients, first.deadline)
        client = await first.clients.get(first.environment)
        assert client is await second.clients.get(second.environment)
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
    source = ResourceListDataSource(ENV, OPTIONS)
    other = ResourceListDataSource(ENV, replace(OPTIONS, namespace="shared-infra"))
    bindings = [ServiceDataSource(SERVICE, source), ServiceDataSource(SERVICE, other)]
    assert all(binding.service == SERVICE for binding in bindings)
    assert bindings[0].source.client_key != bindings[1].source.client_key


def test_kubernetes_environment_extends_environment_with_cluster_access() -> None:
    environment = KubernetesEnvironment(
        name="dev",
        kubeconfig="~/.kube/config",
        context="dev-cluster",
    )

    assert isinstance(environment, Environment)
    assert environment.kubeconfig == "~/.kube/config"
