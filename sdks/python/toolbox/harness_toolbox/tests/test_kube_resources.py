from unittest.mock import AsyncMock

import pytest
from kubernetes_asyncio import client as api
from kubernetes_asyncio.client.exceptions import ApiException

from harness_toolbox.kube import (
    KubernetesClient,
    KubernetesDataSource,
    Options,
    ResourceNotFoundError,
)


def client():
    core = AsyncMock(spec=api.CoreV1Api)
    apps = AsyncMock(spec=api.AppsV1Api)
    # Generated SDK methods return awaitables without being declared async def.
    core.read_namespaced_service = AsyncMock()
    core.list_namespaced_pod = AsyncMock()
    apps.read_namespaced_deployment = AsyncMock()
    core.list_namespaced_pod.return_value = api.V1PodList(
        items=[api.V1Pod(metadata=api.V1ObjectMeta(name="unrelated-prefix", uid="uid-1"))]
    )
    kube = KubernetesClient(KubernetesDataSource(Options("ns", 7, 4)), api=core, apps_api=apps)
    return kube, core, apps


async def test_service_uses_selector_and_shared_api_limits():
    kube, core, _ = client()
    core.read_namespaced_service.return_value = api.V1Service(
        spec=api.V1ServiceSpec(selector={"tier": "api", "app": "worker"})
    )
    pods = await kube.list_service_pods("logical-entry")
    assert pods[0].name == "unrelated-prefix"
    core.read_namespaced_service.assert_awaited_once_with("logical-entry", "ns", _request_timeout=7)
    core.list_namespaced_pod.assert_awaited_once_with(
        "ns", label_selector="app=worker,tier=api", _request_timeout=7
    )
    await kube.dispose()
    with pytest.raises(RuntimeError, match="not initialized"):
        await kube.list_service_pods("logical-entry")


async def test_deployment_preserves_all_selector_expressions():
    kube, core, apps = client()
    selector = api.V1LabelSelector(
        match_labels={"app": "worker"},
        match_expressions=[
            api.V1LabelSelectorRequirement(key="tier", operator="In", values=["b", "a"]),
            api.V1LabelSelectorRequirement(key="stage", operator="NotIn", values=["retired"]),
            api.V1LabelSelectorRequirement(key="active", operator="Exists"),
            api.V1LabelSelectorRequirement(key="disabled", operator="DoesNotExist"),
        ],
    )
    apps.read_namespaced_deployment.return_value = api.V1Deployment(
        spec=api.V1DeploymentSpec(selector=selector, template=api.V1PodTemplateSpec())
    )
    await kube.list_deployment_pods("worker-deployment")
    apps.read_namespaced_deployment.assert_awaited_once_with(
        "worker-deployment", "ns", _request_timeout=7
    )
    assert (
        core.list_namespaced_pod.call_args.kwargs["label_selector"]
        == "app=worker,tier in (a,b),stage notin (retired),active,!disabled"
    )
    await kube.dispose()


@pytest.mark.parametrize("kind", ["service", "deployment"])
@pytest.mark.parametrize("status", [404, 403, 500])
async def test_only_missing_resources_are_not_found(kind, status):
    kube, core, apps = client()
    read = core.read_namespaced_service if kind == "service" else apps.read_namespaced_deployment
    read.side_effect = ApiException(status=status)
    method = kube.list_service_pods if kind == "service" else kube.list_deployment_pods
    with pytest.raises(ResourceNotFoundError if status == 404 else ApiException):
        await method("target")
    core.list_namespaced_pod.assert_not_awaited()
    await kube.dispose()


@pytest.mark.parametrize("kind", ["service", "deployment"])
async def test_empty_selector_never_lists_all_pods(kind):
    kube, core, apps = client()
    core.read_namespaced_service.return_value = api.V1Service(spec=api.V1ServiceSpec())
    apps.read_namespaced_deployment.return_value = api.V1Deployment(
        spec=api.V1DeploymentSpec(selector=api.V1LabelSelector(), template=api.V1PodTemplateSpec())
    )
    method = kube.list_service_pods if kind == "service" else kube.list_deployment_pods
    with pytest.raises(ValueError, match="selector"):
        await method("target")
    core.list_namespaced_pod.assert_not_awaited()
    await kube.dispose()
