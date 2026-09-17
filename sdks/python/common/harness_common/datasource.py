"""Data access associations built on the neutral client provider contract."""

from dataclasses import dataclass
from typing import Generic, Protocol

from harness_common.client import C, ClientProvider
from harness_common.service import Service


class DataSource(ClientProvider[C], Protocol[C]):
    """A source of data; lifecycle and reuse follow ClientProvider.

    Accessible environment providers need not be described as data sources.
    """


@dataclass(frozen=True)
class ServiceDataSource(Generic[C]):
    """Associate business ownership without changing client identity or lifetime."""

    service: Service
    source: DataSource[C]
