"""Execution-scoped, asynchronous client ownership.

DataSource keys describe configuration, not object identity. Consumers receive a
ClientProvider; only the root execution owns and disposes the ClientManager.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
from typing import Protocol, TypeVar, cast


class Client(Protocol):
    async def initialize(self) -> None: ...
    async def dispose(self) -> None: ...


C = TypeVar("C", bound=Client, covariant=True)


class DataSource(Protocol[C]):
    @property
    def key(self) -> str: ...
    def create_client(self, clients: ClientProvider) -> C: ...


class ClientProvider(Protocol):
    async def get(self, source: DataSource[C]) -> C: ...


def data_source_key(protocol: str, configuration: object) -> str:
    """Hash JSON-compatible configuration, including credentials, without exposing it."""
    canonical = json.dumps(configuration, sort_keys=True, separators=(",", ":"), allow_nan=False)
    return protocol + ":" + hashlib.sha256(canonical.encode()).hexdigest()


class ClientManager:
    """Share initialized clients within one event loop and close them at root exit.

    Initialize dependencies through the supplied provider before publishing a
    client as ready. Reverse readiness order then closes consumers first.
    Join all command work before leaving the root context: borrowed clients are
    invalid after disposal. Managers cannot be reopened or shared across loops.
    """

    def __init__(self) -> None:
        self._pending: dict[str, asyncio.Task[Client]] = {}
        self._ready: list[Client] = []
        self._disposal: asyncio.Task[None] | None = None
        self._cleanup_errors: list[BaseException] = []
        self._unclean: set[str] = set()

    async def get(self, source: DataSource[C]) -> C:
        if self._disposal is not None:
            raise RuntimeError("client manager is disposed")
        key = source.key
        task = self._pending.get(key)
        if task is None:
            # Store before the task can run; factories can themselves request dependencies.
            task = asyncio.create_task(self._initialize(source))
            self._pending[key] = task
            task.add_done_callback(lambda done: self._settled(key, done))
        # A cancelled consumer must not cancel initialization needed by other consumers.
        return cast(C, await asyncio.shield(task))

    def _settled(self, key: str, task: asyncio.Task[Client]) -> None:
        failed = task.cancelled() or task.exception() is not None
        if failed and key not in self._unclean and self._pending.get(key) is task:
            del self._pending[key]

    async def _initialize(self, source: DataSource[Client]) -> Client:
        client = source.create_client(self)
        try:
            await client.initialize()
            self._ready.append(client)
            return client
        except BaseException as error:
            try:
                await client.dispose()
            except BaseException as cleanup:
                self._cleanup_errors.append(cleanup)
                self._unclean.add(source.key)
                raise BaseExceptionGroup(
                    "client initialization and cleanup failed", [error, cleanup]
                ) from None
            raise

    async def dispose(self) -> None:
        """Idempotent, concurrent-safe cleanup; cancellation of a waiter cannot stop it."""
        if self._disposal is None:
            self._disposal = asyncio.create_task(self._dispose())
        await asyncio.shield(self._disposal)

    async def _dispose(self) -> None:
        pending = list(self._pending.values())
        for task in pending:
            if not task.done():
                task.cancel()
        await asyncio.gather(*pending, return_exceptions=True)
        self._pending.clear()
        for client in reversed(self._ready):
            try:
                await client.dispose()
            except BaseException as error:
                self._cleanup_errors.append(error)
        self._ready.clear()
        if self._cleanup_errors:
            raise BaseExceptionGroup("client cleanup failed", self._cleanup_errors)

    async def __aenter__(self) -> ClientManager:
        if self._disposal is not None:
            raise RuntimeError("client manager is disposed")
        return self

    async def __aexit__(self, *_: object) -> None:
        await self.dispose()
