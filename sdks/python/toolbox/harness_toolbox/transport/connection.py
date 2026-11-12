"""Caller-owned target resolution and ordered connection paths."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import TYPE_CHECKING, Generic, TypeVar

from harness_toolbox.transport.base import DirectTransport, Transport
from harness_toolbox.transport.pod_python import PodPythonTransport

if TYPE_CHECKING:
    from harness_toolbox.address import AddressPolicy


T = TypeVar("T")


@dataclass(frozen=True)
class ConnectionSource(Generic[T]):
    """Caller-owned configuration; toolbox resolves addresses in the supplied environment."""

    key: str
    resolve: Callable[[], Awaitable[T]]
    transports: tuple[Transport | PodPythonTransport, ...] = (DirectTransport(),)
    addresses: AddressPolicy | None = None
