"""Data access associations built on the neutral client provider contract."""

from dataclasses import dataclass
from typing import Generic, Protocol

from harness_common.access.client import C, ClientProvider
from harness_common.system.service import Service


class DataSource(ClientProvider[C], Protocol[C]):
    """A source of data; lifecycle and reuse follow ClientProvider.

    Accessible environment providers need not be described as data sources.

    @spec kind identifies the backend implementation with a nonempty, open string;
          consumer-owned query strategies may select by kind, not by URL or source name.
    """

    # Backend implementation, not a source name or query-language dialect.
    kind: str

    # Safe discovery metadata, not credentials or part of client_key.
    description: str | None = None


@dataclass(frozen=True)
class ServiceDataSource(Generic[C]):
    """Associate business ownership without changing client identity or lifetime."""

    service: Service
    source: DataSource[C]
