import json
import sys
from types import SimpleNamespace

import pytest
from aiohttp import web
from harness_common import Host, KubernetesEnvironment
from harness_common.client import ClientManager
from kubernetes_asyncio.client import ApiException

from harness_toolbox.kube import (
    KubernetesClientFactory,
    KubernetesResourcesClientFactory,
    Options,
    PodRef,
)


@pytest.fixture(params=[False, True], ids=["local", "host-worker"])
async def cluster(tmp_path, monkeypatch, request):
    remote = request.param
    monkeypatch.setattr(
        "harness_toolbox.kube.worker.command", lambda host, argv: [sys.executable, *argv[1:]]
    )
    state = {"calls": [], "uid": "original", "deny": False, "deleted": False}

    async def serve(request):
        path = request.path
        if path == "/version":
            return web.json_response({"gitVersion": "v1.34.0"})
        if path == "/apis":
            return web.json_response({"kind": "APIGroupList", "groups": []})
        if path == "/api":
            return web.json_response({"kind": "APIVersions", "versions": ["v1"]})
        if path == "/api/v1":
            return web.json_response(
                {
                    "kind": "APIResourceList",
                    "groupVersion": "v1",
                    "resources": [
                        {
                            "name": "pods",
                            "kind": "Pod",
                            "namespaced": True,
                            "verbs": ["get", "list", "create", "delete"],
                        },
                        {
                            "name": "namespaces",
                            "kind": "Namespace",
                            "namespaced": False,
                            "verbs": ["get", "list", "create", "delete"],
                        },
                    ],
                }
            )
        body = await request.json() if request.can_read_body else None
        state["calls"].append((request.method, path, body))
        if request.method == "POST" and state["deny"]:
            return web.json_response(
                {"kind": "Status", "message": "admission denied", "code": 403}, status=403
            )
        if request.method == "DELETE":
            assert body["preconditions"] == {"uid": "original"}
            state["deleted"] = True
            return web.json_response({"kind": "Status", "status": "Success"})
        if state["deleted"]:
            return web.json_response({"kind": "Status", "code": 404}, status=404)
        if path.endswith("/log"):
            return web.Response(body=b"native-api-log\n")
        if request.method == "POST":
            body["metadata"]["uid"] = "original"
            body.setdefault("spec", {})["securityContext"] = {"runAsNonRoot": True}
            return web.json_response(body, status=201)
        return web.json_response(
            {
                "apiVersion": "v1",
                "kind": "Pod",
                "metadata": {"name": "suite", "namespace": "ns", "uid": state["uid"]},
                "status": {"phase": "Failed"},
            }
        )

    app = web.Application()
    app.router.add_route("*", "/{path:.*}", serve)
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "127.0.0.1", 0)
    await site.start()
    port = site._server.sockets[0].getsockname()[1]
    config = tmp_path / "config.json"
    config.write_text(
        json.dumps(
            {
                "apiVersion": "v1",
                "kind": "Config",
                "current-context": "stub",
                "clusters": [{"name": "stub", "cluster": {"server": f"http://127.0.0.1:{port}"}}],
                "contexts": [{"name": "stub", "context": {"cluster": "stub", "user": "stub"}}],
                "users": [{"name": "stub", "user": {}}],
            }
        )
    )
    try:
        async with ClientManager() as clients:
            if remote:
                resources = await clients.get(
                    KubernetesResourcesClientFactory(
                        KubernetesEnvironment(
                            "stub", str(config), host=Host("worker", "ssh", "stub")
                        ),
                        Options("ns", 10, 2),
                    )
                )
                client = SimpleNamespace(
                    resources=resources,
                    wait_completed=resources.wait_completed,
                    read_logs=resources.read_logs,
                    dispose=resources.dispose,
                )
            else:
                client = await clients.get(
                    KubernetesClientFactory(Options("ns", 2, 2), kubeconfig=str(config))
                )
            yield client, state
    finally:
        await runner.cleanup()


async def test_native_manifest_roundtrip_preserves_admission_and_uid(cluster):
    client, state = cluster
    manifest = {
        "apiVersion": "v1",
        "kind": "Pod",
        "metadata": {"name": "suite", "namespace": "ns"},
        "spec": {"containers": [{"name": "suite", "image": "test"}]},
    }
    admitted = await client.resources.create(manifest)
    assert admitted["spec"]["securityContext"]["runAsNonRoot"]
    ref = PodRef("suite", admitted["metadata"]["uid"])
    assert (await client.wait_completed(ref, timeout_s=1, interval_s=0.01)).phase == "Failed"
    assert await client.read_logs(ref, container="suite", max_bytes=100) == b"native-api-log\n"
    await client.resources.delete(admitted)
    await client.resources.wait_deleted(admitted, timeout_s=1, interval_s=0.01)
    assert state["deleted"]


async def test_manifest_does_not_escape_namespace_or_retry_admission(cluster):
    client, state = cluster
    manifest = {
        "apiVersion": "v1",
        "kind": "Pod",
        "metadata": {"name": "suite", "namespace": "other"},
    }
    with pytest.raises(ValueError, match="namespace differs"):
        await client.resources.create(manifest)
    assert not state["calls"]
    manifest["metadata"]["namespace"] = "ns"
    state["deny"] = True
    with pytest.raises(ApiException) as error:
        await client.resources.create(manifest)
    assert error.value.status == 403
    assert len(state["calls"]) == 1


async def test_logs_are_bounded_and_replacement_is_not_observed(cluster):
    client, state = cluster
    ref = PodRef("suite", "original")
    with pytest.raises(ValueError, match="byte limit"):
        await client.read_logs(ref, container="suite", max_bytes=2)
    state["uid"] = "replacement"
    with pytest.raises(RuntimeError, match="identity changed"):
        await client.read_logs(ref, container="suite", max_bytes=100)
    with pytest.raises(RuntimeError, match="identity changed"):
        await client.wait_completed(ref, timeout_s=1, interval_s=0.01)


async def test_cluster_resource_uses_cluster_path_and_borrow_does_not_outlive_client(cluster):
    client, state = cluster
    await client.resources.create(
        {"apiVersion": "v1", "kind": "Namespace", "metadata": {"name": "owned"}}
    )
    assert state["calls"][0][1] == "/api/v1/namespaces"
    await client.dispose()
    with pytest.raises(RuntimeError, match="not initialized|closed"):
        await client.resources.get("v1", "Namespace", "owned")


async def test_delete_requires_observed_uid_and_namespace(cluster):
    client, state = cluster
    manifest = {"apiVersion": "v1", "kind": "Pod", "metadata": {"name": "suite", "namespace": "ns"}}
    with pytest.raises(ValueError, match="UID"):
        await client.resources.delete(manifest)
    manifest["metadata"].update(uid="original", namespace="other")
    with pytest.raises(ValueError, match="namespace"):
        await client.resources.delete(manifest)
    assert state["calls"] == []
