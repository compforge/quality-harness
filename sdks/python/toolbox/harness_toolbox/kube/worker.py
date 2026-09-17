"""Bounded Kubernetes resource protocol over an owned, replaceable SSH process.

Both hosts require the same harness-toolbox[kube] version. Credentials stay on
the access host. Protocol errors retire the channel; operations are never replayed.
"""

from __future__ import annotations

import asyncio
import base64
import json
import logging
import sys
from contextlib import AsyncExitStack
from dataclasses import asdict, replace

from harness_common import ClientManager
from kubernetes_asyncio.client import ApiException

from harness_toolbox.environment import KubernetesEnvironment, _native_access, parse_environment
from harness_toolbox.host import command
from harness_toolbox.kube.model import Options, PodRef
from harness_toolbox.process import process_scope

LOG = logging.getLogger(__name__)
_PROTOCOL = 2


class KubernetesWorkerTransport:
    """Serialize requests on a worker; a failed request affects only its channel."""

    def __init__(self, environment: KubernetesEnvironment, options: Options):
        self.environment = environment
        self.options = options
        self._stack = AsyncExitStack()
        self._process: asyncio.subprocess.Process | None = None
        self._lock = asyncio.Lock()
        self._closed = False
        self._stderr = bytearray()
        self._disposal: asyncio.Task | None = None

    async def initialize(self) -> None:
        async with self._lock:
            await self._ensure_worker()

    async def _ensure_worker(self) -> None:
        if self._closed:
            raise RuntimeError("Kubernetes worker transport is closed")
        if self._process is not None and self._process.returncode is None:
            return
        await self._retire()
        env = self.environment
        assert env.host is not None
        self._stderr.clear()
        try:
            self._process = await self._stack.enter_async_context(
                process_scope(command(env.host, ["python3", "-m", "harness_toolbox.kube.worker"]))
            )
            assert self._process.stderr
            drain = asyncio.create_task(self._drain(self._process.stderr))
            self._stack.push_async_callback(_cancel, drain)
            await self._exchange(
                {
                    "protocol": _PROTOCOL,
                    "environment": {**asdict(replace(env, host=None)), "kind": "kubernetes"},
                    "options": asdict(self.options),
                }
            )
        except BaseException:
            await self._retire()
            raise

    async def _drain(self, stream: asyncio.StreamReader) -> None:
        while chunk := await stream.read(65536):
            self._stderr.extend(chunk[: max(0, 65536 - len(self._stderr))])

    async def request(
        self, operation: str, *, response_timeout_s: float | None = None, **arguments
    ) -> dict | None:
        async with self._lock:
            await self._ensure_worker()
            try:
                response = await self._exchange(
                    {"operation": operation, "arguments": arguments}, timeout_s=response_timeout_s
                )
            except BaseException:
                # Half a frame cannot safely be consumed by the next request.
                await self._retire()
                LOG.info("Kubernetes worker channel retired after interrupted exchange")
                raise
        if "error" in response:
            error = response["error"]
            if error["type"] == "ApiException":
                exc = ApiException(status=error["status"], reason=error["message"])
                exc.body = error.get("body")
                raise exc
            if error["type"] == "ValueError":
                raise ValueError(error["message"])
            if error["type"] == "TimeoutError":
                raise TimeoutError(error["message"])
            raise RuntimeError(
                "Kubernetes operation failed on Environment.host: " + error["message"]
            )
        return response["result"]

    async def _exchange(self, request: dict, *, timeout_s: float | None = None) -> dict:
        process = self._process
        assert process is not None and process.stdin and process.stdout
        try:
            async with asyncio.timeout(
                self.options.request_timeout_s if timeout_s is None else timeout_s
            ):
                process.stdin.write(_encode(request, self.options.max_exec_bytes))
                await process.stdin.drain()
                size = int.from_bytes(await process.stdout.readexactly(4), "big")
                if size > self.options.max_exec_bytes:
                    raise ValueError("Kubernetes worker response exceeds byte limit")
                return json.loads(await process.stdout.readexactly(size))
        except (asyncio.IncompleteReadError, BrokenPipeError, ConnectionResetError) as error:
            raise RuntimeError(
                "Kubernetes worker exited; ensure harness-toolbox[kube] is installed on "
                "Environment.host: " + self._stderr.decode(errors="replace")
            ) from error

    async def _retire(self) -> None:
        stack, self._stack = self._stack, AsyncExitStack()
        self._process = None
        await stack.aclose()

    async def dispose(self) -> None:
        self._closed = True
        if self._disposal is None:
            self._disposal = asyncio.create_task(self._dispose())
        await asyncio.shield(self._disposal)

    async def _dispose(self) -> None:
        async with self._lock:
            await self._retire()


async def _cancel(task: asyncio.Task) -> None:
    task.cancel()
    await asyncio.gather(task, return_exceptions=True)


def _encode(payload: dict, limit: int) -> bytes:
    data = json.dumps(payload).encode()
    if len(data) > limit:
        raise ValueError("Kubernetes worker message exceeds byte limit")
    return len(data).to_bytes(4, "big") + data


def _read(limit: int) -> dict | None:
    header = sys.stdin.buffer.read(4)
    if not header:
        return None
    if len(header) != 4:
        raise EOFError("incomplete Kubernetes worker header")
    size = int.from_bytes(header, "big")
    if size > limit:
        raise ValueError("Kubernetes worker request exceeds byte limit")
    data = sys.stdin.buffer.read(size)
    if len(data) != size:
        raise EOFError("incomplete Kubernetes worker request")
    return json.loads(data)


def _write(payload: dict, limit: int) -> None:
    sys.stdout.buffer.write(_encode(payload, limit))
    sys.stdout.buffer.flush()


async def _serve() -> None:
    setup = _read(65536)
    if setup is None:
        return
    if setup["protocol"] != _PROTOCOL:
        raise ValueError("incompatible Kubernetes worker protocol")
    env = parse_environment(setup["environment"])
    if not isinstance(env, KubernetesEnvironment) or env.host is not None:
        raise ValueError("worker requires a local Kubernetes environment")
    options = Options(**setup["options"])
    async with ClientManager() as clients:
        client = await clients.get(_native_access(env, options))

        async def read_logs(ref: dict, *, container: str, max_bytes: int) -> dict:
            data = await client.read_logs(PodRef(**ref), container=container, max_bytes=max_bytes)
            return {"data": base64.b64encode(data).decode("ascii")}

        async def wait_completed(ref: dict, *, timeout_s: float, interval_s: float) -> dict:
            pod = await client.wait_completed(
                PodRef(**ref), timeout_s=timeout_s, interval_s=interval_s
            )
            return asdict(pod)

        # An explicit allowlist keeps protocol operations on the same native
        # namespace/UID implementation. Never dispatch arbitrary client attributes.
        operations = {
            "create": client.resources.create,
            "get": client.resources.get,
            "list": client.resources.list,
            "delete": client.resources.delete,
            "read_logs": read_logs,
            "wait_completed": wait_completed,
        }
        _write({"result": {}}, options.max_exec_bytes)
        # Idle blocking stdin reads have no API request in flight; EOF owns exit.
        while (request := _read(options.max_exec_bytes)) is not None:
            try:
                operation = request["operation"]
                if operation not in operations:
                    raise ValueError("unsupported Kubernetes worker operation")
                result = await operations[operation](**request["arguments"])
                _write({"result": result}, options.max_exec_bytes)
            except Exception as exc:
                error = {"type": type(exc).__name__, "message": str(exc)}
                if isinstance(exc, ApiException):
                    error.update(type="ApiException", status=exc.status, body=exc.body)
                _write({"error": error}, options.max_exec_bytes)


if __name__ == "__main__":
    asyncio.run(_serve())
