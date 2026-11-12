"""Logical endpoints and the TCP Transport contract."""

from collections.abc import AsyncIterator
from contextlib import AbstractAsyncContextManager, asynccontextmanager
from dataclasses import dataclass
from typing import Protocol


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
