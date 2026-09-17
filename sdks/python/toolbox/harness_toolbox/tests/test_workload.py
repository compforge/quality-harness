import json
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest
from harness_common import KubernetesEnvironment, KubernetesWorkload
from kubernetes_asyncio.client.exceptions import ApiException

from harness_toolbox.errors import ErrorKind, KubernetesError
from harness_toolbox.kube import KubernetesClient, KubernetesClientFactory, Options
from harness_toolbox.kube.environment_resources import (
    KubernetesResourcesClient,
    KubernetesResourcesClientFactory,
)
from harness_toolbox.kube.workload import resolve_workload


def pod(name="pod-a", uid="uid-a", namespace="ns"):
    return {"metadata": {"name": name, "uid": uid, "namespace": namespace}}


def resources():
    result = AsyncMock()
    result.get.return_value = {"spec": {"selector": {"matchLabels": {"app": "api"}}}}
    result.list.return_value = {"items": [pod("pod-b", "uid-b"), pod()]}
    return result


@pytest.mark.parametrize("kind", ["Deployment", "StatefulSet", "DaemonSet", "Pod"])
async def test_resource_resolution(kind):
    access = resources()
    if kind == "Pod":
        access.get.return_value = pod()
    workload = KubernetesWorkload(
        "logical", {"kind": "resource", "resource_kind": kind, "name": "physical"}
    )
    result = await resolve_workload(access, workload, "ns", "env-a")
    assert result[0].pod == "pod-a"
    assert result[0].uid == "uid-a"
    assert result[0].workload == "logical"
    assert result[0].environment == "env-a"
    assert len(result) == (1 if kind == "Pod" else 2)
    access.get.assert_awaited_once_with("v1" if kind == "Pod" else "apps/v1", kind, "physical")


async def test_selectors_keep_expressions_and_empty_results():
    access = resources()
    access.get.return_value = {
        "spec": {
            "selector": {
                "matchLabels": {"app": "api"},
                "matchExpressions": [
                    {"key": "tier", "operator": "In", "values": ["b", "a"]},
                    {"key": "disabled", "operator": "DoesNotExist"},
                ],
            }
        }
    }
    access.list.return_value = {"items": []}
    workload = KubernetesWorkload(
        "api", {"kind": "resource", "resource_kind": "Deployment", "name": "api"}
    )
    assert await resolve_workload(access, workload, "ns", "env") == []
    access.list.assert_awaited_once_with(
        "v1", "Pod", label_selector="app=api,tier in (a,b),!disabled"
    )


@pytest.mark.parametrize(
    "location",
    [
        {"kind": "service", "name": "api"},
        {"kind": "labels", "labels": {"app": "api"}},
    ],
)
async def test_service_and_labels(location):
    access = resources()
    access.get.return_value = {"spec": {"selector": {"app": "api"}}}
    result = await resolve_workload(access, KubernetesWorkload("api", location), "ns", "env")
    assert len(result) == 2
    access.list.assert_awaited_once_with("v1", "Pod", label_selector="app=api")


@pytest.mark.parametrize(
    "status,kind",
    [
        (401, ErrorKind.AUTHENTICATION_FAILED),
        (403, ErrorKind.PERMISSION_DENIED),
        (404, ErrorKind.RESOURCE_NOT_FOUND),
        (429, ErrorKind.LIMIT_EXCEEDED),
        (500, ErrorKind.OPERATION_FAILED),
    ],
)
async def test_api_error_kind_and_safe_message(status, kind):
    access = resources()
    original = ApiException(status=status, reason="secret-token")
    access.list.side_effect = original
    with pytest.raises(KubernetesError) as caught:
        await resolve_workload(
            access,
            KubernetesWorkload("api", {"kind": "labels", "labels": {"app": "api"}}),
            "ns",
            "env",
        )
    assert caught.value.kind == kind
    assert caught.value.code == status
    assert caught.value.__cause__ is original
    assert "secret-token" not in str(caught.value)


async def test_selectorless_service_and_invalid_pod_are_not_empty_inventory():
    access = resources()
    access.get.return_value = {"spec": {}}
    with pytest.raises(KubernetesError) as caught:
        await resolve_workload(
            access, KubernetesWorkload("api", {"kind": "service", "name": "api"}), "ns", "env"
        )
    assert caught.value.kind == ErrorKind.UNSUPPORTED_OPERATION
    access.list.assert_not_called()
    access.list.return_value = {"items": [pod(uid="")]}
    with pytest.raises(KubernetesError) as caught:
        await resolve_workload(
            access,
            KubernetesWorkload("api", {"kind": "labels", "labels": {"app": "api"}}),
            "ns",
            "env",
        )
    assert caught.value.kind == ErrorKind.INVALID_RESPONSE


async def test_namespace_override_borrows_native_pool():
    client = KubernetesClient(KubernetesClientFactory(Options("ns", 2, 3)))
    access = resources()
    access.list.return_value = {"items": [pod(namespace="override")]}
    with patch("harness_toolbox.kube.client.KubernetesResources", return_value=access) as factory:
        result = await client.resolve_workload(
            KubernetesWorkload(
                "api",
                {"kind": "labels", "labels": {"app": "api"}},
                namespace="override",
            ),
            environment="env",
        )
    assert result[0].namespace == "override"
    assert factory.call_args.args[0] == client._native_api
    assert factory.call_args.args[1] == Options("override", 2, 3)


async def test_environment_override_uses_managed_backend_and_keeps_identity():
    clients = AsyncMock()
    scoped = resources()
    scoped.list.return_value = {"items": [pod(namespace="override")]}
    clients.get.return_value = scoped
    source = KubernetesResourcesClientFactory(
        KubernetesEnvironment("env", "/config", "ctx"), Options("ns", 2, 3)
    )
    client = KubernetesResourcesClient(source, clients)
    result = await client.resolve_workload(
        KubernetesWorkload(
            "api",
            {"kind": "labels", "labels": {"app": "api"}},
            namespace="override",
        ),
        environment="env",
    )
    requested = clients.get.call_args.args[0]
    assert requested.environment == source.environment
    assert requested.options == Options("override", 2, 3)
    assert result[0].namespace == "override"
    assert result[0].environment == "env"


async def test_timeout_is_not_empty_inventory():
    access = resources()
    access.list.side_effect = TimeoutError()
    with pytest.raises(KubernetesError) as caught:
        await resolve_workload(
            access,
            KubernetesWorkload(
                "api",
                {"kind": "labels", "labels": {"app": "api"}},
            ),
            "ns",
            "env",
        )
    assert caught.value.kind == ErrorKind.TIMEOUT


async def test_client_uses_default_namespace_and_preserves_container():
    client = KubernetesClient(KubernetesClientFactory(Options("ns", 2, 3), context_name="ctx"))
    client.resources = resources()
    instance = (
        await client.resolve_workload(
            KubernetesWorkload(
                "api",
                {"kind": "labels", "labels": {"app": "api"}},
                container="sidecar",
            ),
            environment="cluster-a/context-a",
        )
    )[0]
    assert instance.container == "sidecar"
    assert instance.namespace == "ns"
    other = KubernetesClient(KubernetesClientFactory(Options("ns", 2, 3), context_name="other"))
    other.resources = resources()
    assert (
        instance.environment
        != (
            await other.resolve_workload(
                KubernetesWorkload(
                    "api",
                    {"kind": "labels", "labels": {"app": "api"}},
                ),
                environment="cluster-b/context-a",
            )
        )[0].environment
    )


async def test_shared_target_identity_is_independent_of_access_configuration():
    fixture = json.loads(
        (Path(__file__).resolve().parents[5] / "conformance/workloads.json").read_text()
    )
    workload = KubernetesWorkload("api", {"kind": "labels", "labels": {"app": "api"}})
    instances = []
    keys = []
    for binding in fixture["target_bindings"]:
        config = binding["access"]
        factory = KubernetesClientFactory(
            Options("ns", 2, 3), kubeconfig=config["kubeconfig"], context_name=config["context"]
        )
        keys.append(factory.key)
        native = KubernetesClient(factory)
        native.resources = resources()
        resolved = await native.resolve_workload(workload, environment=binding["environment"])
        wrapper = KubernetesResourcesClient(
            KubernetesResourcesClientFactory(
                KubernetesEnvironment("display-only", config["kubeconfig"], config["context"]),
                factory.options,
            ),
            AsyncMock(),
        )
        wrapper._native = native
        assert (
            await wrapper.resolve_workload(workload, environment=binding["environment"]) == resolved
        )
        instances.append(resolved[0])
    assert keys[0] != keys[1]
    assert instances[0] == instances[1]
    assert instances[0] != instances[2]
    assert instances[0].environment == fixture["instances"][0]["environment"]


async def test_empty_target_identity_fails_before_access():
    access = resources()
    with pytest.raises(KubernetesError) as caught:
        await resolve_workload(
            access,
            KubernetesWorkload("api", {"kind": "labels", "labels": {"app": "api"}}),
            "ns",
            " ",
        )
    assert caught.value.kind == ErrorKind.INVALID_ARGUMENT
    access.list.assert_not_awaited()
