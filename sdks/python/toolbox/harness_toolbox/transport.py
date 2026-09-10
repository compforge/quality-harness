"""Connection paths preserve target identity; protocol clients own the protocol."""

from __future__ import annotations

import asyncio
import json
import re
from collections.abc import AsyncIterator, Awaitable, Callable, Sequence
from contextlib import AbstractAsyncContextManager, asynccontextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Generic, Protocol, TypeVar

from harness_toolbox.process import process_scope, read_bounded, run


@dataclass(frozen=True)
class Endpoint:
    host: str
    port: int
    servername: str | None = None


class Transport(Protocol):
    @property
    def key(self) -> str: ...
    def connect(self, target: Endpoint) -> AbstractAsyncContextManager[Endpoint]: ...


@dataclass(frozen=True)
class DirectTransport:
    key: str = "direct"

    @asynccontextmanager
    async def connect(self, target: Endpoint) -> AsyncIterator[Endpoint]:
        yield target


@dataclass(frozen=True)
class KubernetesAccess:
    kubeconfig: str | None
    namespace: str
    context: str | None = None
    kubectl: str = "kubectl"

    def command(self, *args: str) -> list[str]:
        command = [self.kubectl, "-n", self.namespace]
        if self.kubeconfig:
            command += ["--kubeconfig", str(Path(self.kubeconfig).expanduser())]
        if self.context:
            command += ["--context", self.context]
        return command + list(args)

    async def execute(
        self,
        pod: str,
        command: Sequence[str],
        *,
        stdin: bytes = b"",
        timeout_s: float = 60,
        max_bytes: int = 64 * 1024 * 1024,
    ) -> bytes:
        return await run(
            self.command("exec", "-i", pod, "--", *command),
            stdin=stdin,
            timeout_s=timeout_s,
            max_bytes=max_bytes,
        )


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
                yield endpoint
            finally:
                for task in (stderr, stdout):
                    if task is not None:
                        task.cancel()
                await asyncio.gather(
                    *(t for t in (stderr, stdout) if t is not None), return_exceptions=True
                )


@dataclass(frozen=True)
class PodPythonTransport:
    access: KubernetesAccess
    pod: str
    python: str = "python3"

    @property
    def key(self) -> str:
        return repr(self)

    async def run(
        self,
        script: str,
        payload: object,
        *,
        timeout_s: float = 60,
        max_bytes: int = 64 * 1024 * 1024,
    ) -> bytes:
        # Credentials and SQL travel over stdin, never process arguments or a shell.
        return await self.access.execute(
            self.pod,
            [self.python, "-c", script],
            stdin=json.dumps(payload).encode(),
            timeout_s=timeout_s,
            max_bytes=max_bytes,
        )


T = TypeVar("T")


@dataclass(frozen=True)
class ConnectionSource(Generic[T]):
    """The consumer owns environment resolution and a stable, complete identity."""

    key: str
    resolve: Callable[[], Awaitable[T]]
    transports: tuple[Transport | PodPythonTransport, ...] = (DirectTransport(),)
