"""Run-owned TCP relay in an explicitly selected existing Kubernetes Workload."""

from __future__ import annotations

import asyncio
import json
import logging
import math
from collections.abc import AsyncIterator
from contextlib import AsyncExitStack, asynccontextmanager, suppress
from dataclasses import asdict, dataclass, replace
from pathlib import Path

from harness_common import KubernetesWorkload, KubernetesWorkloadInstance, client_key
from harness_common.access.client import _ClientBorrower

from harness_toolbox.environment import KubernetesEnvironment
from harness_toolbox.process import process_scope
from harness_toolbox.transport.base import Endpoint
from harness_toolbox.transport.kubernetes import KubernetesAccess
from harness_toolbox.transport.port_forward import PortForwardTransport, _PortForward

LOG = logging.getLogger(__name__)


@dataclass(frozen=True)
class PodRelayTransport:
    """Provider for TCP access from an existing Pod to logical cluster destinations.

    @spec A Service ClusterIP remains a Service destination, rather than pinning
    requests to one of its Pods. HTTP Host/TLS identity stay with the HTTP client.
    @rule No direct fallback or business replay. A lost control session fails the
    client; create a new execution scope to select another relay Pod.
    @link component://sdks/python/toolbox/docs/transport.md#pod-relay
    """

    environment: KubernetesEnvironment
    workload: KubernetesWorkload
    startup_timeout_s: float = 30
    connect_timeout_s: float = 10
    max_connections: int = 128
    max_targets: int = 16
    max_candidate_pods: int = 3
    heartbeat_s: float = 5
    lease_s: float = 30

    def __post_init__(self) -> None:
        """@rule Empty and explicit local transports follow the common Host contract."""
        if self.environment.host is not None and self.environment.host.transport not in {
            "",
            "local",
        }:
            raise ValueError("pod-relay requires local Kubernetes access")
        if not self.environment.name.strip():
            raise ValueError("pod-relay requires an environment identity")
        if not (self.workload.namespace or self.environment.options.namespace).strip():
            raise ValueError("pod-relay requires a namespace")
        budgets = (self.startup_timeout_s, self.connect_timeout_s, self.heartbeat_s, self.lease_s)
        if any(type(v) not in (int, float) or not math.isfinite(v) or v <= 0 for v in budgets):
            raise ValueError("pod-relay budgets must be finite and positive")
        if self.lease_s < 3 * self.heartbeat_s:
            raise ValueError("pod-relay lease must allow at least three heartbeats")
        if any(
            type(v) is not int or v < 1
            for v in (self.max_connections, self.max_targets, self.max_candidate_pods)
        ):
            raise ValueError("pod-relay capacities must be positive integers")

    @property
    def client_key(self) -> str:
        return client_key("pod-relay", {**asdict(self), "environment": self.environment.client_key})

    def create_client(self, clients: _ClientBorrower) -> PodRelayClient:
        return PodRelayClient(self, clients)


class PodRelayClient:
    """One leased helper and its tunnels; root ClientManager owns disposal."""

    def __init__(self, source: PodRelayTransport, clients: _ClientBorrower):
        self.source = source
        self.key = source.client_key
        self._clients = clients
        self._scope = AsyncExitStack()
        self._lock = asyncio.Lock()
        self._process: asyncio.subprocess.Process | None = None
        self._tasks: list[asyncio.Task] = []
        self._pending: dict[int, asyncio.Future] = {}
        self._targets: dict[tuple[str, int], tuple[int, _PortForward]] = {}
        self._next_id = 0
        self._closed = False
        self._failure: str | None = None
        self._instance: KubernetesWorkloadInstance | None = None
        self._container: str | None = None
        self._pid: int | None = None
        self._stderr = bytearray()
        self._ready: asyncio.Future | None = None
        namespace = source.workload.namespace or source.environment.options.namespace
        self._environment = replace(
            source.environment, options=replace(source.environment.options, namespace=namespace)
        )
        self._access = KubernetesAccess(
            self._environment.kubeconfig or None, namespace, self._environment.context
        )

    @property
    def diagnostics(self) -> dict:
        """Detached non-secret evidence; Pod identity is not a readiness promise."""
        return {
            "transport": "pod-relay",
            "instance": asdict(self._instance) if self._instance else None,
            "container": self._container,
            "helper_pid": self._pid,
            "targets": [{"host": h, "port": p} for h, p in self._targets],
            "failure": self._failure,
            "closed": self._closed,
            "lease_s": self.source.lease_s,
        }

    async def initialize(self) -> None:
        async with asyncio.timeout(self.source.startup_timeout_s):
            resources = await self._clients.get(self._environment)
            instances = await resources.resolve_workload(
                self.source.workload, environment=self._environment.name
            )
            tried = 0
            for instance in sorted(instances, key=lambda i: i.pod):
                pod = await resources.get("v1", "Pod", instance.pod)
                if (
                    pod["metadata"]["uid"] != instance.uid
                    or pod["metadata"].get("deletionTimestamp")
                    or pod.get("status", {}).get("phase") != "Running"
                ):
                    continue
                ready = [
                    c["name"] for c in pod["status"].get("containerStatuses", []) if c.get("ready")
                ]
                containers = (
                    [instance.container]
                    if instance.container in ready
                    else ([] if instance.container else ready)
                )
                if not containers:
                    continue
                tried += 1
                for container in containers:
                    for executable in ("python3", "python"):
                        try:
                            await self._access.execute(
                                instance.pod,
                                [
                                    executable,
                                    "-c",
                                    "import sys; sys.exit(sys.version_info < (3, 8))",
                                ],
                                container=container,
                                timeout_s=min(5, self.source.startup_timeout_s),
                                max_bytes=2048,
                            )
                        except (RuntimeError, TimeoutError):
                            continue
                        self._instance, self._container = instance, container
                        await self._verify()
                        await self._start(executable)
                        await self._verify()
                        LOG.info(
                            "pod relay ready: pod=%s uid=%s container=%s helper_pid=%s",
                            instance.pod,
                            instance.uid,
                            container,
                            self._pid,
                        )
                        return
                if tried >= self.source.max_candidate_pods:
                    break
            raise ConnectionError("No ready relay container with Python >=3.8 in selected Workload")

    async def _verify(self) -> None:
        assert self._instance is not None
        resources = await self._clients.get(self._environment)
        pod = await resources.get("v1", "Pod", self._instance.pod)
        if (
            pod["metadata"]["uid"] != self._instance.uid
            or pod["metadata"].get("deletionTimestamp")
            or pod.get("status", {}).get("phase") != "Running"
        ):
            raise ConnectionError("relay Pod was replaced or is terminating")

    async def _start(self, executable: str) -> None:
        assert self._instance is not None and self._container is not None
        config = {
            "connectTimeoutMs": self.source.connect_timeout_s * 1000,
            "maxConnections": self.source.max_connections,
            "maxTargets": self.source.max_targets,
            "leaseMs": self.source.lease_s * 1000,
        }
        script = Path(__file__).with_name("_pod_relay_worker.py").read_text()
        command = self._access.command(
            "exec",
            "-i",
            self._instance.pod,
            "-c",
            self._container,
            "--",
            executable,
            "-u",
            "-c",
            script,
            json.dumps(config),
        )
        self._process = await self._scope.enter_async_context(process_scope(command))
        self._ready = asyncio.get_running_loop().create_future()
        self._tasks = [asyncio.create_task(self._read()), asyncio.create_task(self._read_stderr())]
        self._scope.push_async_callback(self._stop_tasks)
        await self._ready
        self._tasks.append(asyncio.create_task(self._heartbeat()))

    async def _stop_tasks(self) -> None:
        for task in self._tasks:
            task.cancel()
        await asyncio.gather(*self._tasks, return_exceptions=True)

    def _fail(self, reason: str) -> None:
        if self._closed or self._failure is not None:
            return
        self._failure = reason
        LOG.error("pod relay failed: pod=%s reason=%s", self._instance.pod, reason)
        for future in [self._ready, *self._pending.values()]:
            if future is not None and not future.done():
                future.set_exception(ConnectionError(reason))
        # Break existing sockets as well as future connects, without replaying bytes.
        for _, forward in self._targets.values():
            if forward.process.returncode is None:
                with suppress(ProcessLookupError):
                    forward.process.terminate()
        if self._process is not None and self._process.stdin is not None:
            self._process.stdin.close()

    async def _read(self) -> None:
        assert self._process is not None and self._process.stdout is not None
        try:
            while line := await self._process.stdout.readline():
                message = json.loads(line)
                if not isinstance(message, dict):
                    raise ValueError("expected control object")
                if message.get("ready") is True and not self._ready.done():
                    self._pid = int(message["pid"])
                    self._ready.set_result(None)
                else:
                    future = self._pending.get(message.get("id"))
                    if future is None:
                        raise ValueError("unexpected control response")
                    if not future.done():
                        future.set_result(message)
            self._fail("relay control channel closed")
        except (ValueError, OSError, KeyError, TypeError):
            self._fail("invalid relay control channel")

    async def _read_stderr(self) -> None:
        assert self._process is not None and self._process.stderr is not None
        while data := await self._process.stderr.read(4096):
            self._stderr.extend(data)
            del self._stderr[:-8192]

    async def _send(self, message: dict) -> None:
        if self._closed or self._failure:
            raise ConnectionError(self._failure or "relay is closed")
        assert self._process is not None and self._process.stdin is not None
        try:
            self._process.stdin.write(json.dumps(message).encode() + b"\n")
            await self._process.stdin.drain()
        except (BrokenPipeError, ConnectionResetError):
            self._fail("relay control write failed")
            raise ConnectionError("relay control write failed") from None

    async def _heartbeat(self) -> None:
        try:
            while not self._closed and self._failure is None:
                await asyncio.sleep(self.source.heartbeat_s)
                await self._send({"heartbeat": True})
        except (ConnectionError, OSError):
            self._fail("relay heartbeat failed")

    @asynccontextmanager
    async def connect(self, target: Endpoint) -> AsyncIterator[Endpoint]:
        async with self._lock:
            if self._closed or self._failure or self._ready is None or not self._ready.done():
                raise ConnectionError(self._failure or "relay is not active")
            key = (target.host, target.port)
            cached = self._targets.get(key)
            if cached is not None and not cached[1].alive:
                self._fail("relay port-forward exited")
                raise ConnectionError(self._failure)
            if cached is None:
                if len(self._targets) >= self.source.max_targets:
                    raise ConnectionError("pod-relay target limit reached")
                async with asyncio.timeout(self.source.startup_timeout_s):
                    await self._verify()
                    self._next_id += 1
                    identifier = self._next_id
                    future = asyncio.get_running_loop().create_future()
                    self._pending[identifier] = future
                    try:
                        await self._send(
                            {"id": identifier, "host": target.host, "port": target.port}
                        )
                        response = await future
                        if "error" in response:
                            raise ConnectionError("relay listener unavailable")
                        remote_port = int(response["port"])
                        if not 0 < remote_port < 65536:
                            raise ConnectionError("invalid relay port")
                        forward = PortForwardTransport(
                            self._access,
                            f"pod/{self._instance.pod}",
                            remote_port,
                            self.source.startup_timeout_s,
                        )
                        async with AsyncExitStack() as pending:
                            tunnel = await pending.enter_async_context(forward._open(target))
                            await self._verify()
                            if self._failure:
                                raise ConnectionError(self._failure)
                            self._scope.push_async_exit(pending.pop_all())
                            cached = self._targets[key] = (remote_port, tunnel)
                    except BaseException:
                        # The remote listener may already exist. Retiring this session
                        # bounds unknown state rather than creating duplicate listeners.
                        self._fail("relay target preparation failed")
                        raise
                    finally:
                        self._pending.pop(identifier, None)
                        if not future.done():
                            future.cancel()
            local = cached[1].endpoint
        yield Endpoint(local.host, local.port, target.servername or target.host)

    async def dispose(self) -> None:
        if self._closed:
            return
        self._closed = True
        for future in self._pending.values():
            if not future.done():
                future.set_exception(ConnectionError("relay is closed"))
        # Serialize with target creation before draining the owning stack.
        async with self._lock:
            if self._process is not None and self._process.stdin is not None:
                self._process.stdin.close()
                with suppress(TimeoutError):
                    await asyncio.wait_for(self._process.wait(), 2)
            await self._scope.aclose()
        LOG.info(
            "pod relay closed: pod=%s helper_pid=%s targets=%s lease_s=%s",
            self._instance.pod if self._instance else None,
            self._pid,
            len(self._targets),
            self.source.lease_s,
        )
