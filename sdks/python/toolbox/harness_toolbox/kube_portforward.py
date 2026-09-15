"""Execution-scoped Kubernetes connections for Service IPs and dynamic Pod IPs."""

from __future__ import annotations

import asyncio
import json
import logging
from collections.abc import AsyncIterator
from contextlib import AsyncExitStack, asynccontextmanager

from harness_toolbox.process import run
from harness_toolbox.transport import Endpoint, KubernetesAccess, PortForwardTransport, _PortForward

LOG = logging.getLogger(__name__)


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
        return f"kubernetes-port-forward:{self.access!r}"

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
