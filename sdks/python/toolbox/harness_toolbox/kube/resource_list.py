"""Read-only view of the shared environment resource backend."""

from __future__ import annotations

from dataclasses import dataclass, replace

from harness_common import DataSource, client_key
from harness_common.client import _ClientBorrower

from harness_toolbox.data_loader import DataLoader
from harness_toolbox.environment import KubernetesEnvironment
from harness_toolbox.kube.environment_resources import (
    KubernetesResourcesClient,
)
from harness_toolbox.kube.model import Options


@dataclass(frozen=True)
class ResourceListDataSource(DataSource["ResourceListClient"]):
    environment: KubernetesEnvironment
    options: Options

    @property
    def client_key(self) -> str:
        return client_key(
            "kubernetes-resource-list",
            replace(self.environment, options=self.options).client_key,
        )

    def create_client(self, clients: _ClientBorrower) -> ResourceListClient:
        return ResourceListClient(self, clients)


class ResourceListClient:
    """Borrow resources without owning their pool or worker lifetime."""

    def __init__(self, source: ResourceListDataSource, clients: _ClientBorrower):
        self._source = source
        self._clients = clients
        self._resources: KubernetesResourcesClient | None = None
        self._closed = False

    async def initialize(self) -> None:
        self._resources = await self._clients.get(
            replace(self._source.environment, options=self._source.options)
        )

    async def list(
        self,
        api_version: str,
        kind: str,
        *,
        label_selector: str = "",
        scope: DataLoader | None = None,
    ) -> dict:
        if self._closed:
            raise RuntimeError("resource list client is closed")
        if scope is None:
            return await self._list(api_version, kind, label_selector=label_selector)
        return await scope.read(
            (self._source.client_key, "list", api_version, kind, label_selector),
            lambda: self._list(api_version, kind, label_selector=label_selector),
        )

    async def _list(self, api_version: str, kind: str, *, label_selector: str = "") -> dict:
        if self._resources is None:
            raise RuntimeError("resource list client is not initialized")
        return await self._resources.list(api_version, kind, label_selector=label_selector)

    async def dispose(self) -> None:
        self._closed = True
