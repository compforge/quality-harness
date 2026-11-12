"""Owned Kubernetes tunnels with fixed or UID-resolved targets."""

from __future__ import annotations

import asyncio
import json
import logging
import re
from collections.abc import AsyncIterator
from contextlib import AsyncExitStack, asynccontextmanager
from dataclasses import dataclass

from harness_toolbox.process import process_scope, read_bounded, run
from harness_toolbox.transport.base import Endpoint
from harness_toolbox.transport.kubernetes import KubernetesAccess

LOG = logging.getLogger(__name__)


@dataclass(frozen=True)
class _PortForward:
    endpoint: Endpoint
    process: asyncio.subprocess.Process
    stdout: asyncio.Task
    stderr: asyncio.Task

    @property
    def alive(self) -> bool:
        return self.process.returncode is None and not self.stdout.done() and not self.stderr.done()


@dataclass(frozen=True)
class PortForwardTransport:
    access: KubernetesAccess
    resource: str
    remote_port: int
    timeout_s: float = 15

    @property
    def key(self) -> str:
        return repr(self)

    @asynccontextmanager
    async def connect(self, target: Endpoint) -> AsyncIterator[Endpoint]:
        async with self._open(target) as tunnel:
            yield tunnel.endpoint

    @asynccontextmanager
    async def _open(self, target: Endpoint) -> AsyncIterator[_PortForward]:
        # Let kubectl allocate an ephemeral local port; preallocating creates a bind race.
        command = self.access.command(
            "port-forward", "--address", "127.0.0.1", self.resource, f":{self.remote_port}"
        )
        async with process_scope(command) as process:
            assert process.stdout and process.stderr
            stderr = asyncio.create_task(read_bounded(process.stderr, 65536))

            async def ready() -> Endpoint:
                assert process.stdout
                while line := await process.stdout.readline():
                    match = re.search(rb"Forwarding from 127\.0\.0\.1:(\d+)", line)
                    if match:
                        return Endpoint(
                            "127.0.0.1", int(match[1]), target.servername or target.host
                        )
                raise ConnectionError("port-forward exited before becoming ready")

            stdout: asyncio.Task[bytes] | None = None
            try:
                endpoint = await asyncio.wait_for(ready(), self.timeout_s)
                stdout = asyncio.create_task(read_bounded(process.stdout, 1024 * 1024))
                yield _PortForward(endpoint, process, stdout, stderr)
            finally:
                for task in (stderr, stdout):
                    if task is not None:
                        task.cancel()
                await asyncio.gather(
                    *(t for t in (stderr, stdout) if t is not None), return_exceptions=True
                )


class KubernetesPortForwardTransport:
    """Reuse tunnels within one explicit namespace; never fall back to local routing.

    Use as an async context manager. Service targets are registered by
    ``service_endpoint``; other destinations must resolve to a unique live Pod IP.
    Host-network Pods are excluded because their IP does not identify one Pod.
    This transport does not establish a workload-to-runner reverse connection.
    """

    def __init__(self, access: KubernetesAccess, *, timeout_s: float = 15):
        if not access.namespace:
            raise ValueError("port-forward requires an explicit namespace")
        self.access = access
        self.timeout_s = timeout_s
        self._lock = asyncio.Lock()
        self._services: dict[tuple[str, int], tuple[str, str]] = {}
        self._tunnels: dict[tuple[str, str, int], tuple[_PortForward, AsyncExitStack]] = {}
        self._active = False
        self._closed = False

    @property
    def key(self) -> str:
        return f"kubernetes-port-forward:{self.access!r}:{self.timeout_s}"

    @property
    def client_key(self) -> str:
        return self.key

    def create_client(self, clients) -> KubernetesPortForwardTransport:
        return KubernetesPortForwardTransport(self.access, timeout_s=self.timeout_s)

    async def initialize(self) -> None:
        await self.__aenter__()

    async def dispose(self) -> None:
        if self._active:
            await self.__aexit__(None, None, None)

    async def __aenter__(self) -> KubernetesPortForwardTransport:
        if self._active or self._closed:
            raise RuntimeError("transport scope is already active or closed")
        self._active = True
        return self

    async def __aexit__(self, *exc) -> None:
        self._active = False
        self._closed = True
        async with self._lock:
            tunnels, self._tunnels = self._tunnels, {}
            self._services.clear()
            async with AsyncExitStack() as closing:
                for _, owned in tunnels.values():
                    closing.push_async_exit(owned)
        LOG.info("port-forward scope closed: tunnels=%s", len(tunnels))

    async def _get(self, resource: str) -> dict:
        return json.loads(
            await run(self.access.command("get", resource, "-o", "json"), timeout_s=self.timeout_s)
        )

    async def service_endpoint(self, name: str, port: int) -> Endpoint:
        """Resolve a Service in this namespace and retain its logical ClusterIP."""
        if not self._active:
            raise RuntimeError("transport scope is not active")
        resource = f"service/{name}"
        service = await self._get(resource)
        host = service["spec"].get("clusterIP")
        if not host or host == "None":
            raise ValueError("port-forward requires a Service with a ClusterIP")
        if not any(p["port"] == port for p in service["spec"]["ports"]):
            raise ValueError("port is not declared by the selected Service")
        self._services[(host, port)] = (resource, service["metadata"]["uid"])
        return Endpoint(host, port)

    async def pod_endpoint(self, name: str, uid: str, port: int) -> Endpoint:
        """Bind an already resolved WorkloadInstance to its physical Pod incarnation."""
        if not self._active:
            raise RuntimeError("transport scope is not active")
        resource = f"pod/{name}"
        pod = await self._get(resource)
        if pod["metadata"]["uid"] != uid or pod["metadata"].get("deletionTimestamp"):
            raise ConnectionError("selected Pod was replaced or is terminating")
        address = pod.get("status", {}).get("podIP")
        if not address or pod["status"].get("phase") != "Running":
            raise ConnectionError("selected Pod is not running with an address")
        self._services[(address, port)] = (resource, uid)
        return Endpoint(address, port)

    async def _resource(self, target: Endpoint) -> tuple[str, str]:
        service = self._services.get((target.host, target.port))
        if service:
            resource, uid = service
            current = await self._get(resource)
            if current["metadata"]["uid"] != uid:
                raise ConnectionError("selected Service was replaced")
            return service
        pods = await self._get("pods")
        matches = [
            p
            for p in pods["items"]
            if p["status"].get("podIP") == target.host
            and not p["spec"].get("hostNetwork", False)
            and not p["metadata"].get("deletionTimestamp")
            and p["status"].get("phase") == "Running"
        ]
        if len(matches) != 1:
            raise ConnectionError("destination is not a unique live Pod in the selected namespace")
        pod = matches[0]
        return f"pod/{pod['metadata']['name']}", pod["metadata"]["uid"]

    @asynccontextmanager
    async def connect(self, target: Endpoint) -> AsyncIterator[Endpoint]:
        if not self._active:
            raise RuntimeError("transport scope is not active")
        # Resolve identity on each new client connection: Pod IPs may be reused
        # during a long run. Cached sockets are keyed by resource UID, not IP alone.
        async with self._lock:
            if not self._active:
                raise RuntimeError("transport scope is not active")
            resource, uid = await self._resource(target)
            key = (resource, uid, target.port)
            cached = self._tunnels.get(key)
            if cached is not None and not cached[0].alive:
                await cached[1].aclose()
                del self._tunnels[key]
                cached = None
                LOG.info("retired exited port-forward: resource=%s uid=%s", resource, uid)
            if cached is None:
                forward = PortForwardTransport(self.access, resource, target.port, self.timeout_s)
                async with AsyncExitStack() as pending:
                    tunnel = await pending.enter_async_context(forward._open(target))
                    local = tunnel.endpoint
                    current = await self._get(resource)
                    if current["metadata"]["uid"] != uid:
                        raise ConnectionError("port-forward target changed during startup")
                    owned = pending.pop_all()
                    self._tunnels[key] = (tunnel, owned)
                LOG.info(
                    "port-forward %s:%s -> %s uid=%s localhost:%s",
                    target.host,
                    target.port,
                    resource,
                    uid,
                    local.port,
                )
            else:
                local = cached[0].endpoint
        yield Endpoint(local.host, local.port, target.servername or target.host)
