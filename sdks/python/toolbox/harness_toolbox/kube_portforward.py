"""Execution-scoped Kubernetes connections for Service IPs and dynamic Pod IPs."""

from __future__ import annotations

import asyncio
import json
import logging
from collections.abc import AsyncIterator
from contextlib import AsyncExitStack, asynccontextmanager

from harness_toolbox.process import run
from harness_toolbox.transport import Endpoint, KubernetesAccess, PortForwardTransport

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
        self._stack = AsyncExitStack()
        self._lock = asyncio.Lock()
        self._services: dict[tuple[str, int], tuple[str, str]] = {}
        self._endpoints: dict[tuple[str, str, int], Endpoint] = {}
        self._active = False

    @property
    def key(self) -> str:
        return f"kubernetes-port-forward:{self.access!r}"

    async def __aenter__(self) -> KubernetesPortForwardTransport:
        if self._active:
            raise RuntimeError("transport scope is already active")
        self._active = True
        await self._stack.__aenter__()
        return self

    async def __aexit__(self, *exc) -> None:
        self._active = False
        await self._stack.__aexit__(*exc)
        LOG.info("port-forward scope closed: tunnels=%s", len(self._endpoints))
        self._endpoints.clear()
        self._services.clear()

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
            resource, uid = await self._resource(target)
            key = (resource, uid, target.port)
            local = self._endpoints.get(key)
            if local is None:
                forward = PortForwardTransport(self.access, resource, target.port, self.timeout_s)
                async with AsyncExitStack() as pending:
                    local = await pending.enter_async_context(forward.connect(target))
                    current = await self._get(resource)
                    if current["metadata"]["uid"] != uid:
                        raise ConnectionError("port-forward target changed during startup")
                    self._stack.push_async_exit(pending.pop_all())
                self._endpoints[key] = local
                LOG.info(
                    "port-forward %s:%s -> %s uid=%s localhost:%s",
                    target.host,
                    target.port,
                    resource,
                    uid,
                    local.port,
                )
        yield Endpoint(local.host, local.port, target.servername or target.host)
