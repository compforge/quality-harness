"""One explicit scope of shared read results, failures and in-flight work."""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable, Hashable
from typing import TypeVar, cast

T = TypeVar("T")


class DataLoader:
    """Deduplicate read-only operations until scope exit; never own their clients.

    Adapters own keys, including source identity and every query parameter. The
    same key must always produce the same result type. Returned values are shared
    observations and must not be mutated by consumers. Exceptions are shared too.
    """

    def __init__(self) -> None:
        self._reads: dict[Hashable, asyncio.Task] = {}
        self._active = False
        self._closing: asyncio.Task[None] | None = None

    async def read(self, key: Hashable, operation: Callable[[], Awaitable[T]]) -> T:
        if not self._active:
            raise RuntimeError("read scope is not active")
        task = self._reads.get(key)
        if task is None:

            async def invoke() -> T:
                return await operation()

            task = asyncio.create_task(invoke())
            self._reads[key] = task
        # One cancelled consumer must not cancel another consumer's shared read.
        # Scope exit owns cancellation and joining of unfinished operations.
        return cast(T, await asyncio.shield(task))

    async def aclose(self) -> None:
        self._active = False
        if self._closing is None:
            self._closing = asyncio.create_task(self._close())
        await asyncio.shield(self._closing)

    async def _close(self) -> None:
        tasks = list(self._reads.values())
        for task in tasks:
            if not task.done():
                task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        self._reads.clear()

    async def __aenter__(self) -> DataLoader:
        if self._active or self._closing is not None:
            raise RuntimeError("read scope cannot be reopened")
        self._active = True
        return self

    async def __aexit__(self, *_: object) -> None:
        await self.aclose()
