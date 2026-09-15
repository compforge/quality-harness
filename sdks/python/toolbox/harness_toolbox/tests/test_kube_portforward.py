import asyncio
import json
from contextlib import asynccontextmanager
from types import SimpleNamespace
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
        async def _open(self, target):
            opened.append(self.resource)
            try:
                yield SimpleNamespace(
                    endpoint=Endpoint("127.0.0.1", 12000 + len(opened)), alive=True
                )
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


async def test_dead_service_tunnel_reopens_without_changing_service_uid(monkeypatch):
    import sys

    from harness_toolbox import transport as transports
    from harness_toolbox.process import process_scope

    processes = []

    @asynccontextmanager
    async def worker(command):
        # A real child owns a listening socket and the readiness stream, as kubectl does.
        script = "import socket,time; s=socket.socket(); s.bind(('127.0.0.1',0)); s.listen(); print('Forwarding from 127.0.0.1:'+str(s.getsockname()[1]),flush=True); time.sleep(60)"
        async with process_scope([sys.executable, "-c", script]) as process:
            processes.append(process)
            yield process

    monkeypatch.setattr(transports, "process_scope", worker)
    service = {
        "metadata": {"uid": "same-service"},
        "spec": {"clusterIP": "10.0.0.1", "ports": [{"port": 80}]},
    }
    monkeypatch.setattr(module, "run", AsyncMock(return_value=json.dumps(service).encode()))
    transport = KubernetesPortForwardTransport(KubernetesAccess("config", "ns"))
    async with transport:
        target = await transport.service_endpoint("api", 80)
        async with transport.connect(target) as first:
            _, writer = await asyncio.open_connection(first.host, first.port)
            writer.close()
            await writer.wait_closed()
        processes[0].terminate()
        await processes[0].wait()
        async with transport.connect(target) as second:
            assert len(processes) == 2
            _, writer = await asyncio.open_connection(second.host, second.port)
            writer.close()
            await writer.wait_closed()
        async with transport.connect(target):
            assert len(processes) == 2
    assert all(p.returncode is not None for p in processes)
    with pytest.raises(RuntimeError, match="closed"):
        await transport.__aenter__()
