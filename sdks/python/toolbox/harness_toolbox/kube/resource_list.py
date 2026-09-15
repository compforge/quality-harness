"""Execution-scoped native resource lists, local or on an Environment Host.

The SSH worker is read-only and retains its Kubernetes pool until stdin closes.
Both hosts need harness-toolbox[kube] from the same version on Python's path.
"""

from __future__ import annotations

import asyncio
import json
import sys
from contextlib import AsyncExitStack, suppress
from dataclasses import asdict, dataclass, replace
from typing import TYPE_CHECKING

from harness_common.client import ClientManager, ClientProvider, data_source_key
from harness_common.environment import KubernetesEnvironment, parse_environment

from harness_toolbox.environment import kubernetes_source
from harness_toolbox.host import command
from harness_toolbox.kube.model import Options
from harness_toolbox.process import process_scope

if TYPE_CHECKING:
    from harness_toolbox.kube.client import KubernetesClient

_PROTOCOL = 1


@dataclass(frozen=True)
class ResourceListDataSource:
    """Read manifests through one namespace-scoped client on the access host.

    Remote access requires ``python3 -m harness_toolbox.kube.resource_list``.
    No kubeconfig contents or credentials are copied to the runner.
    """

    environment: KubernetesEnvironment
    options: Options

    @property
    def key(self) -> str:
        # Logical environment/host names do not fragment physical pool ownership.
        host = self.environment.host
        return data_source_key(
            "kubernetes-resource-list",
            [
                self.environment.kubeconfig,
                self.environment.context,
                host.transport if host else "local",
                host.address if host else "",
                asdict(self.options),
            ],
        )

    def create_client(self, clients: ClientProvider) -> ResourceListClient:
        return ResourceListClient(self, clients)


class ResourceListClient:
    def __init__(self, source: ResourceListDataSource, clients: ClientProvider) -> None:
        self._source = source
        self._clients = clients
        self._native: KubernetesClient | None = None
        self._process: asyncio.subprocess.Process | None = None
        self._stack = AsyncExitStack()
        self._lock = asyncio.Lock()
        self._closed = False
        self._disposal: asyncio.Task[None] | None = None
        self._stderr = bytearray()

    async def initialize(self) -> None:
        env = self._source.environment
        if env.host is None or env.host.transport == "local":
            self._native = await self._clients.get(kubernetes_source(env, self._source.options))
            return
        argv = command(env.host, ["python3", "-m", "harness_toolbox.kube.resource_list"])
        self._process = await self._stack.enter_async_context(process_scope(argv))
        assert self._process.stderr
        drain = asyncio.create_task(self._drain_stderr(self._process.stderr))
        self._stack.push_async_callback(_cancel, drain)
        await self._exchange(
            {
                "protocol": _PROTOCOL,
                "environment": {**asdict(replace(env, host=None)), "kind": "kubernetes"},
                "options": asdict(self._source.options),
            }
        )

    async def _drain_stderr(self, stream: asyncio.StreamReader) -> None:
        while chunk := await stream.read(65536):
            self._stderr.extend(chunk[: max(0, 65536 - len(self._stderr))])

    async def list(self, api_version: str, kind: str, *, label_selector: str = "") -> dict:
        if self._closed:
            raise RuntimeError("resource list client is closed")
        if self._native is not None:
            return await self._native.resources.list(
                api_version, kind, label_selector=label_selector
            )
        return await self._exchange(
            {
                "api_version": api_version,
                "kind": kind,
                "label_selector": label_selector,
            }
        )

    async def _exchange(self, request: dict) -> dict:
        process = self._process
        if process is None or self._closed:
            raise RuntimeError("resource list worker is unavailable")
        assert process.stdin and process.stdout
        # A timeout/cancellation can leave half a response. Retire that session;
        # never let a later query consume a previous query's bytes.
        async with self._lock:
            if self._closed:
                raise RuntimeError("resource list client is closed")
            try:
                async with asyncio.timeout(self._source.options.request_timeout_s):
                    process.stdin.write(_encode(request, self._source.options.max_exec_bytes))
                    await process.stdin.drain()
                    size = int.from_bytes(await process.stdout.readexactly(4), "big")
                    if size > self._source.options.max_exec_bytes:
                        raise ValueError("resource list response exceeds byte limit")
                    response = json.loads(await process.stdout.readexactly(size))
            except BaseException as error:
                await self.dispose()
                if isinstance(error, asyncio.IncompleteReadError):
                    raise RuntimeError(
                        "resource list worker exited; ensure harness-toolbox[kube] is installed "
                        "on Environment.host: " + self._stderr.decode(errors="replace")
                    ) from error
                raise
        if "error" in response:
            raise RuntimeError("resource list failed on Environment.host: " + response["error"])
        return response["result"]

    async def dispose(self) -> None:
        self._closed = True
        if self._disposal is None:
            self._disposal = asyncio.create_task(self._stack.aclose())
        await asyncio.shield(self._disposal)


async def _cancel(task: asyncio.Task) -> None:
    task.cancel()
    with suppress(asyncio.CancelledError):
        await task


def _encode(payload: dict, limit: int) -> bytes:
    data = json.dumps(payload).encode()
    if len(data) > limit:
        raise ValueError("resource list message exceeds byte limit")
    return len(data).to_bytes(4, "big") + data


def _read(limit: int) -> dict | None:
    header = sys.stdin.buffer.read(4)
    if not header:
        return None
    if len(header) != 4:
        raise EOFError("incomplete resource list header")
    size = int.from_bytes(header, "big")
    if size > limit:
        raise ValueError("resource list request exceeds byte limit")
    data = sys.stdin.buffer.read(size)
    if len(data) != size:
        raise EOFError("incomplete resource list request")
    return json.loads(data)


def _write(payload: dict, limit: int) -> None:
    sys.stdout.buffer.write(_encode(payload, limit))
    sys.stdout.buffer.flush()


async def _serve() -> None:
    # Sequential stdio requests are the only input to this read-only worker.
    # Blocking reads happen while no API operation is in flight; EOF owns exit.
    setup = _read(65536)
    if setup is None:
        return
    if setup["protocol"] != _PROTOCOL:
        raise ValueError("incompatible resource list protocol")
    env = parse_environment(setup["environment"])
    if not isinstance(env, KubernetesEnvironment):
        raise ValueError("resource list requires a Kubernetes environment")
    options = Options(**setup["options"])
    async with ClientManager() as clients:
        client = await clients.get(kubernetes_source(env, options))
        _write({"result": {}}, options.max_exec_bytes)
        while (request := _read(options.max_exec_bytes)) is not None:
            try:
                result = await client.resources.list(
                    request["api_version"],
                    request["kind"],
                    label_selector=request.get("label_selector", ""),
                )
                _write({"result": result}, options.max_exec_bytes)
            except Exception as error:  # preserve query failures without replaying the request
                _write({"error": f"{type(error).__name__}: {error}"}, options.max_exec_bytes)


if __name__ == "__main__":
    asyncio.run(_serve())
