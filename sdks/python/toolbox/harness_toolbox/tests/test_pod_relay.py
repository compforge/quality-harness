import asyncio
import json
import sys
from contextlib import asynccontextmanager
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace

import pytest
from harness_common import ClientManager, KubernetesWorkload, KubernetesWorkloadInstance

from harness_toolbox.environment import KubernetesEnvironment
from harness_toolbox.http import HTTPClientProvider
from harness_toolbox.process import process_scope
from harness_toolbox.transport import Endpoint, pod_relay


@pytest.fixture
def relay_platform(monkeypatch):
    """Stub only Kubernetes boundaries; execute the shipped helper and real TCP/HTTP."""
    state = SimpleNamespace(
        uid="uid-1",
        processes=[],
        forwards=[],
        opened=0,
        block_forward=None,
        python_missing=False,
        probe_count=0,
    )
    env = KubernetesEnvironment("qa", "/unused/config")
    workload = KubernetesWorkload("relay", {"kind": "service", "name": "utility"}, "quality")
    state.source = pod_relay.PodRelayTransport(env, workload, heartbeat_s=0.1, lease_s=0.6)

    class Resources:
        async def initialize(self):
            pass

        async def dispose(self):
            pass

        async def resolve_workload(self, workload, *, environment):
            return [
                KubernetesWorkloadInstance(
                    environment, workload.name, "quality", "relay-1", "uid-1"
                )
            ]

        async def get(self, api_version, kind, name):
            return {
                "metadata": {"name": name, "uid": state.uid},
                "status": {
                    "phase": "Running",
                    "containerStatuses": [{"name": "app", "ready": True}],
                },
            }

    monkeypatch.setattr(KubernetesEnvironment, "create_client", lambda self, clients: Resources())

    async def execute(self, pod, command, **kwargs):
        state.probe_count += 1
        assert self.namespace == "quality" and kwargs["container"] == "app"
        if state.python_missing:
            raise RuntimeError("missing python")
        return b""

    @asynccontextmanager
    async def spawn(command):
        assert command[:3] == ["kubectl", "-n", "quality"]
        index = command.index("--")
        assert "exec" in command and "-i" in command
        async with process_scope([sys.executable, *command[index + 2 :]]) as process:
            state.processes.append(process)
            yield process

    @asynccontextmanager
    async def forward(self, target):
        assert self.resource == "pod/relay-1"
        state.opened += 1
        if state.block_forward:
            state.block_forward.set()
            await asyncio.Event().wait()
        tunnel = SimpleNamespace(
            endpoint=Endpoint("127.0.0.1", self.remote_port),
            alive=True,
            process=state.processes[-1],
        )
        state.forwards.append(tunnel)
        try:
            yield tunnel
        finally:
            tunnel.alive = False

    monkeypatch.setattr(pod_relay.KubernetesAccess, "execute", execute)
    monkeypatch.setattr(pod_relay, "process_scope", spawn)
    monkeypatch.setattr(pod_relay.PortForwardTransport, "_open", forward)
    return state


@asynccontextmanager
async def sse_backend():
    tasks, requests = set(), []
    first, last = b"data: start\n\n", b"data: end\n\n"

    async def serve(reader, writer):
        task = asyncio.current_task()
        tasks.add(task)
        try:
            while True:
                request = await reader.readuntil(b"\r\n\r\n")
                requests.append(request)
                writer.write(
                    f"HTTP/1.1 200 OK\r\nContent-Type: text/event-stream\r\nContent-Length: {len(first) + len(last)}\r\n\r\n".encode()
                    + first
                )
                await writer.drain()
                await asyncio.sleep(0.02)
                writer.write(last)
                await writer.drain()
        except (asyncio.IncompleteReadError, ConnectionError):
            pass
        finally:
            writer.close()
            await writer.wait_closed()
            tasks.discard(task)

    server = await asyncio.start_server(serve, "127.0.0.1", 0)
    try:
        yield server.sockets[0].getsockname()[1], requests
    finally:
        server.close()
        await server.wait_closed()
        for task in list(tasks):
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)


async def test_relay_streams_concurrent_http_reuses_target_and_records_identity(relay_platform):
    """@case Relay preserves streamed bytes, logical Host and root cleanup at >128 connections."""
    state = relay_platform
    source = replace(state.source, max_connections=160)
    async with sse_backend() as (port, requests):
        async with ClientManager() as clients:
            relay = await clients.get(source)
            http = await clients.get(HTTPClientProvider(150, 10, transport=source))
            assert relay is await clients.get(source)

            async def read():
                chunks = []
                async with http.stream("GET", f"http://localhost:{port}/stream") as response:
                    assert response.status_code == 200
                    async for chunk in response.aiter_bytes():
                        chunks.append(chunk)
                assert b"".join(chunks) == b"data: start\n\ndata: end\n\n"
                return len(chunks)

            streamed = await asyncio.gather(*(read() for _ in range(150)))
            assert max(streamed) > 1
            assert state.opened == 1
            assert all(f"Host: localhost:{port}".encode() in r for r in requests)
            facts = relay.diagnostics
            assert facts["instance"]["uid"] == "uid-1"
            assert facts["container"] == "app" and facts["helper_pid"] == state.processes[0].pid
            facts["targets"].clear()
            assert len(relay.diagnostics["targets"]) == 1
            assert not relay.diagnostics["closed"]
        assert http.is_closed and relay.diagnostics["closed"]
        assert all(p.returncode is not None for p in state.processes)
        assert all(not f.alive for f in state.forwards)
        with pytest.raises(ConnectionError, match="not active"):
            async with relay.connect(Endpoint("localhost", port)):
                pass


async def test_missing_python_fails_before_business_connections(relay_platform):
    relay_platform.python_missing = True
    async with ClientManager() as clients:
        with pytest.raises(ConnectionError, match="No ready relay"):
            await clients.get(relay_platform.source)
    assert relay_platform.probe_count == 2
    assert relay_platform.processes == []


async def test_replaced_uid_rejected_before_target_creation(relay_platform):
    state = relay_platform
    async with ClientManager() as clients:
        relay = await clients.get(state.source)
        state.uid = "replacement"
        with pytest.raises(ConnectionError, match="replaced"):
            async with relay.connect(Endpoint("localhost", 1234)):
                pass
        assert not state.opened
    assert state.processes[0].returncode is not None


async def test_target_limit_and_dead_control_have_no_direct_fallback(relay_platform):
    state = relay_platform
    async with ClientManager() as clients:
        relay = await clients.get(replace(state.source, max_targets=1))
        async with relay.connect(Endpoint("localhost", 1234)):
            pass
        with pytest.raises(ConnectionError, match="target limit"):
            async with relay.connect(Endpoint("localhost", 1235)):
                pass
        state.processes[0].kill()
        await state.processes[0].wait()
        await asyncio.sleep(0)
        with pytest.raises(ConnectionError):
            async with relay.connect(Endpoint("localhost", 1234)):
                pass
    assert state.opened == 1


async def test_cancel_during_target_forward_retires_helper_and_joins_tasks(relay_platform):
    state = relay_platform
    state.block_forward = asyncio.Event()
    async with ClientManager() as clients:
        relay = await clients.get(state.source)

        async def connect():
            async with relay.connect(Endpoint("localhost", 1234)):
                pytest.fail("cancelled setup must not become a connection")

        task = asyncio.create_task(connect())
        await asyncio.wait_for(state.block_forward.wait(), 2)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert relay.diagnostics["failure"] == "relay target preparation failed"
    assert state.processes[0].returncode is not None
    assert all(task.done() for task in relay._tasks)


async def test_worker_lease_reaps_listeners_and_active_sockets():
    script = Path(pod_relay.__file__).with_name("_pod_relay_worker.py")
    config = {"connectTimeoutMs": 500, "maxConnections": 2, "maxTargets": 2, "leaseMs": 250}
    async with (
        sse_backend() as (port, _),
        process_scope([sys.executable, "-u", str(script), json.dumps(config)]) as process,
    ):
        ready = json.loads(await process.stdout.readline())
        assert ready["pid"] == process.pid
        process.stdin.write(
            json.dumps({"id": 1, "host": "localhost", "port": port}).encode() + b"\n"
        )
        await process.stdin.drain()
        reply = json.loads(await process.stdout.readline())
        reader, writer = await asyncio.open_connection("127.0.0.1", reply["port"])
        try:
            # No heartbeats and no EOF: lease alone must terminate the helper.
            await asyncio.wait_for(process.wait(), 2)
            assert process.returncode == 0
            assert await asyncio.wait_for(reader.read(), 1) == b""
            with pytest.raises(OSError):
                await asyncio.open_connection("127.0.0.1", reply["port"])
        finally:
            writer.close()
            await writer.wait_closed()


@pytest.mark.parametrize(
    "options",
    [
        {"startup_timeout_s": float("inf")},
        {"max_connections": 0},
        {"max_targets": True},
        {"heartbeat_s": 10, "lease_s": 15},
    ],
)
def test_invalid_relay_budgets_fail_without_io(relay_platform, options):
    with pytest.raises(ValueError):
        replace(relay_platform.source, **options)
