"""Data access associations built on the neutral client factory contract."""

from dataclasses import dataclass
from typing import Generic, Protocol

from harness_common.client import C, ClientFactory
from harness_common.service import Service


class DataSource(ClientFactory[C], Protocol[C]):
    """A source of data; lifecycle and reuse follow ClientFactory.

    Environment management factories need not be described as data sources.
    """


@dataclass(frozen=True)
class ServiceDataSource(Generic[C]):
    """Associate business ownership without changing client identity or lifetime."""

    service: Service
    source: DataSource[C]
