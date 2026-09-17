import asyncio
import json
import sys
from dataclasses import replace

import pytest
from aiohttp import web
from harness_common import Host, KubernetesWorkload
from harness_common.client import ClientManager
from kubernetes_asyncio.client import ApiException

from harness_toolbox.environment import KubernetesEnvironment
from harness_toolbox.kube import Options
from harness_toolbox.kube.resource_list import ResourceListDataSource


@pytest.fixture
async def source(tmp_path):
    state = {"requests": [], "deny": False, "delay": 0, "size": 0}

    async def serve(request):
        if request.path == "/version":
            return web.json_response({"gitVersion": "v1.34.0"})
        if request.path == "/apis":
            return web.json_response({"kind": "APIGroupList", "groups": []})
        if request.path == "/api":
            return web.json_response({"kind": "APIVersions", "versions": ["v1"]})
        if request.path == "/api/v1":
            return web.json_response(
                {
                    "kind": "APIResourceList",
                    "groupVersion": "v1",
                    "resources": [
                        {"name": "pods", "kind": "Pod", "namespaced": True, "verbs": ["list"]}
                    ],
                }
            )
        state["requests"].append(
            (request.method, request.path, dict(request.query), request.transport)
        )
        await asyncio.sleep(state["delay"])
        if state["deny"]:
            return web.json_response(
                {"kind": "Status", "message": "denied", "code": 403}, status=403
            )
        return web.json_response(
            {"apiVersion": "v1", "kind": "PodList", "items": [], "padding": "x" * state["size"]}
        )

    app = web.Application()
    app.router.add_route("*", "/{path:.*}", serve)
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "127.0.0.1", 0)
    await site.start()
    port = site._server.sockets[0].getsockname()[1]
    path = tmp_path / "remote-config.json"
    path.write_text(
        json.dumps(
            {
                "apiVersion": "v1",
                "kind": "Config",
                "current-context": "wrong",
                "clusters": [{"name": "stub", "cluster": {"server": f"http://127.0.0.1:{port}"}}],
                "contexts": [{"name": "selected", "context": {"cluster": "stub", "user": "stub"}}],
                "users": [{"name": "stub", "user": {}}],
            }
        )
    )
    env = KubernetesEnvironment("stub", str(path), "selected")
    try:
        yield ResourceListDataSource(env, Options("test-namespace", 10, 2)), state
    finally:
        await runner.cleanup()


@pytest.mark.parametrize("remote", [False, True])
async def test_workload_resolution_uses_environment_backend(source, monkeypatch, remote):
    config, state = source
    if remote:
        config, _ = remote_source(config, monkeypatch)
    async with ClientManager() as clients:
        access = await clients.get(replace(config.environment, options=config.options))
        workload = KubernetesWorkload(
            "logical", {"kind": "labels", "labels": {"app": "api"}}, namespace="override"
        )
        assert await access.resolve_workload(workload, environment="env") == []
        assert state["requests"][-1][1] == "/api/v1/namespaces/override/pods"
        state["deny"] = True
        from harness_toolbox.errors import ErrorKind, KubernetesError

        with pytest.raises(KubernetesError) as caught:
            await access.resolve_workload(workload, environment="env")
        assert caught.value.kind == ErrorKind.PERMISSION_DENIED


def remote_source(source, monkeypatch):
    calls = []
    from harness_toolbox.host import command

    def local_worker(host, argv):
        calls.append(command(host, argv))
        return [sys.executable, *argv[1:]]

    monkeypatch.setattr("harness_toolbox.kube.worker.command", local_worker)
    return replace(
        source, environment=replace(source.environment, host=Host("devbox", "ssh", "devbox"))
    ), calls


@pytest.mark.parametrize("remote", [False, True])
async def test_resource_list_reuses_pool_and_closes(source, monkeypatch, remote):
    config, state = source
    if remote:
        config, calls = remote_source(config, monkeypatch)
    async with ClientManager() as clients:
        client = await clients.get(config)
        same_access = replace(config, environment=replace(config.environment, name="alias"))
        assert client is await clients.get(same_access)
        for selector in ["app=chat", "app=worker"]:
            result = await client.list("v1", "Pod", label_selector=selector)
            assert result["kind"] == "PodList"
        assert len(state["requests"]) == 2
        assert [r[:3] for r in state["requests"]] == [
            ("GET", "/api/v1/namespaces/test-namespace/pods", {"labelSelector": "app=chat"}),
            ("GET", "/api/v1/namespaces/test-namespace/pods", {"labelSelector": "app=worker"}),
        ]
        assert state["requests"][0][3] is state["requests"][1][3]
        if remote:
            assert len(calls) == 1 and calls[0][0] == "ssh"
            process = client._resources._transport._process
    with pytest.raises(RuntimeError, match="closed"):
        await client.list("v1", "Pod")
    if remote:
        assert process.returncode is not None


@pytest.mark.parametrize("remote", [False, True])
async def test_api_errors_remain_errors_and_are_not_replayed(source, monkeypatch, remote):
    config, state = source
    if remote:
        config, _ = remote_source(config, monkeypatch)
    async with ClientManager() as clients:
        client = await clients.get(config)
        state["deny"] = True
        with pytest.raises((RuntimeError, ApiException), match="403"):
            await client.list("v1", "Pod")
        assert len(state["requests"]) == 1
        state["deny"] = False
        assert (await client.list("v1", "Pod"))["items"] == []


@pytest.mark.parametrize("cancel", [False, True])
async def test_remote_interrupted_response_retires_session(source, monkeypatch, cancel):
    config, state = source
    config, _ = remote_source(config, monkeypatch)
    async with ClientManager() as clients:
        client = await clients.get(config)
        # Initialize before lowering the budget: the test targets an in-flight read.
        transport = client._resources._transport
        process = transport._process
        transport.options = replace(config.options, request_timeout_s=0.1)
        state["delay"] = 0.4
        task = asyncio.create_task(client.list("v1", "Pod"))
        if cancel:
            while not state["requests"]:
                await asyncio.sleep(0.001)
            task.cancel()
        with pytest.raises(asyncio.CancelledError if cancel else TimeoutError):
            await task
        assert process.returncode is not None
        assert len(state["requests"]) == 1
        state["delay"] = 0
        transport.options = config.options
        assert client is await clients.get(config)
        assert (await client.list("v1", "Pod"))["items"] == []
        assert len(state["requests"]) == 2
        assert transport._process is not process


async def test_remote_response_limit(source, monkeypatch):
    config, state = source
    config, _ = remote_source(config, monkeypatch)
    config = replace(config, options=replace(config.options, max_exec_bytes=4096))
    async with ClientManager() as clients:
        client = await clients.get(config)
        state["size"] = 5000
        with pytest.raises(ValueError, match="byte limit"):
            await client.list("v1", "Pod")


async def test_missing_remote_dependency_reports_failure(source, monkeypatch):
    config, _ = source
    config, _ = remote_source(config, monkeypatch)
    monkeypatch.setattr(
        "harness_toolbox.kube.worker.command",
        lambda *args: [
            sys.executable,
            "-c",
            "import sys; sys.stderr.write('No module named harness_toolbox'); sys.exit(1)",
        ],
    )
    async with ClientManager() as clients:
        with pytest.raises(RuntimeError, match="harness-toolbox"):
            await clients.get(config)


@pytest.mark.parametrize("remote", [False, True])
async def test_read_view_borrows_shared_backend(source, monkeypatch, remote):
    config, state = source
    if remote:
        config, calls = remote_source(config, monkeypatch)
    async with ClientManager() as clients:
        view = await clients.get(config)
        resources = await clients.get(replace(config.environment, options=config.options))
        assert view._resources is resources
        await view.list("v1", "Pod")
        await view.dispose()
        await resources.list("v1", "Pod")
        assert state["requests"][0][3] is state["requests"][1][3]
        if remote:
            assert len(calls) == 1
