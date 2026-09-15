import asyncio
import json
from contextlib import asynccontextmanager
from unittest.mock import AsyncMock

import pytest

from harness_toolbox import kube_portforward as module
from harness_toolbox.kube_portforward import KubernetesPortForwardTransport
from harness_toolbox.transport import Endpoint, KubernetesAccess


def pod(uid="uid-1", **spec):
    return {
        "metadata": {"name": "worker", "uid": uid},
        "spec": spec,
        "status": {"podIP": "10.0.0.2", "phase": "Running"},
    }


@pytest.fixture
def forwards(monkeypatch):
    opened, closed = [], []

    class Forward:
        def __init__(self, access, resource, port, timeout):
            self.resource = resource

        @asynccontextmanager
        async def connect(self, target):
            opened.append(self.resource)
            try:
                yield Endpoint("127.0.0.1", 12000 + len(opened))
            finally:
                closed.append(self.resource)

    monkeypatch.setattr(module, "PortForwardTransport", Forward)
    return opened, closed


async def test_reuses_tunnel_and_closes_scope(monkeypatch, forwards):
    current = pod()

    async def get(command, **kwargs):
        return json.dumps({"items": [current]} if "pods" in command else current).encode()

    monkeypatch.setattr(module, "run", get)
    async with KubernetesPortForwardTransport(KubernetesAccess("config", "ns")) as transport:

        async def connect():
            async with transport.connect(Endpoint("10.0.0.2", 8080, "logical")) as endpoint:
                return endpoint

        first, second = await asyncio.gather(connect(), connect())
        assert first == second
        assert first.servername == "logical"
        assert forwards[0] == ["pod/worker"]
    assert forwards[1] == ["pod/worker"]


@pytest.mark.parametrize("items", [[], [pod(hostNetwork=True)], [pod(), pod()]])
async def test_rejects_ambiguous_or_unscoped_destinations(monkeypatch, items):
    monkeypatch.setattr(
        module, "run", AsyncMock(return_value=json.dumps({"items": items}).encode())
    )
    async with KubernetesPortForwardTransport(KubernetesAccess("config", "ns")) as transport:
        with pytest.raises(ConnectionError, match="unique live Pod"):
            async with transport.connect(Endpoint("10.0.0.2", 8080)):
                pytest.fail("must not route directly")


async def test_identity_change_during_startup_closes_tunnel(monkeypatch, forwards):
    monkeypatch.setattr(
        module,
        "run",
        AsyncMock(
            side_effect=[
                json.dumps({"items": [pod()]}).encode(),
                json.dumps(pod("replacement")).encode(),
            ]
        ),
    )
    async with KubernetesPortForwardTransport(KubernetesAccess("config", "ns")) as transport:
        with pytest.raises(ConnectionError, match="changed during startup"):
            async with transport.connect(Endpoint("10.0.0.2", 8080)):
                pass
        assert forwards[1] == ["pod/worker"]


async def test_service_uses_explicit_namespace_and_identity(monkeypatch, forwards):
    service = {
        "metadata": {"uid": "service-uid"},
        "spec": {"clusterIP": "10.0.0.1", "ports": [{"port": 8090}]},
    }
    run = AsyncMock(return_value=json.dumps(service).encode())
    monkeypatch.setattr(module, "run", run)
    access = KubernetesAccess("config", "selected", "context")
    async with KubernetesPortForwardTransport(access) as transport:
        target = await transport.service_endpoint("api", 8090)
        async with transport.connect(target):
            assert forwards[0] == ["service/api"]
    assert all(
        call.args[0] == access.command("get", "service/api", "-o", "json")
        for call in run.await_args_list
    )


async def test_scope_required():
    transport = KubernetesPortForwardTransport(KubernetesAccess("config", "ns"))
    with pytest.raises(RuntimeError, match="not active"):
        await transport.service_endpoint("api", 80)
