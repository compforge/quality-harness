"""Read-only view of the shared environment resource backend."""

from __future__ import annotations

from dataclasses import dataclass

from harness_common import ClientProvider, KubernetesEnvironment, data_source_key

from harness_toolbox.data_loader import DataLoader
from harness_toolbox.kube.environment_resources import (
    KubernetesResourcesClient,
    KubernetesResourcesDataSource,
)
from harness_toolbox.kube.model import Options


@dataclass(frozen=True)
class ResourceListDataSource:
    environment: KubernetesEnvironment
    options: Options

    @property
    def key(self) -> str:
        return data_source_key(
            "kubernetes-resource-list",
            KubernetesResourcesDataSource(self.environment, self.options).key,
        )

    def create_client(self, clients: ClientProvider) -> ResourceListClient:
        return ResourceListClient(self, clients)


class ResourceListClient:
    """Borrow resources without owning their pool or worker lifetime."""

    def __init__(self, source: ResourceListDataSource, clients: ClientProvider):
        self._source = source
        self._clients = clients
        self._resources: KubernetesResourcesClient | None = None
        self._closed = False

    async def initialize(self) -> None:
        self._resources = await self._clients.get(
            KubernetesResourcesDataSource(self._source.environment, self._source.options)
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
            (self._source.key, "list", api_version, kind, label_selector),
            lambda: self._list(api_version, kind, label_selector=label_selector),
        )

    async def _list(self, api_version: str, kind: str, *, label_selector: str = "") -> dict:
        if self._resources is None:
            raise RuntimeError("resource list client is not initialized")
        return await self._resources.list(api_version, kind, label_selector=label_selector)

    async def dispose(self) -> None:
        self._closed = True
