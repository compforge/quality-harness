"""One manifest API for local and Host-native Kubernetes access."""

from __future__ import annotations

import base64
from dataclasses import asdict, dataclass
from typing import TYPE_CHECKING

from harness_common import ClientProvider, KubernetesEnvironment, data_source_key

from harness_toolbox.environment import kubernetes_source
from harness_toolbox.kube.model import Container, Options, Pod, PodRef
from harness_toolbox.kube.resources import wait_deleted

if TYPE_CHECKING:
    from harness_toolbox.kube.client import KubernetesClient
    from harness_toolbox.kube.worker import KubernetesWorkerTransport


@dataclass(frozen=True)
class KubernetesResourcesDataSource:
    environment: KubernetesEnvironment
    options: Options

    @property
    def key(self) -> str:
        host = self.environment.host
        return data_source_key(
            "kubernetes-resources",
            [
                self.environment.kubeconfig,
                self.environment.context,
                host.transport if host else "local",
                host.address if host else "",
                asdict(self.options),
            ],
        )

    def create_client(self, clients: ClientProvider) -> KubernetesResourcesClient:
        return KubernetesResourcesClient(self, clients)


class KubernetesResourcesClient:
    """Borrow the local pool or own a remote transport; both use native resources."""

    def __init__(self, source: KubernetesResourcesDataSource, clients: ClientProvider):
        self._source = source
        self._clients = clients
        self._native: KubernetesClient | None = None
        self._transport: KubernetesWorkerTransport | None = None
        self._closed = False

    async def initialize(self) -> None:
        env = self._source.environment
        if env.host is None or env.host.transport == "local":
            client = await self._clients.get(kubernetes_source(env, self._source.options))
            self._native = client
        else:
            from harness_toolbox.kube.worker import KubernetesWorkerTransport

            self._transport = KubernetesWorkerTransport(env, self._source.options)
            await self._transport.initialize()

    async def _call(self, operation: str, **arguments):
        if self._closed:
            raise RuntimeError("Kubernetes resources client is closed")
        if self._native is not None:
            return await getattr(self._native.resources, operation)(**arguments)
        if self._transport is None:
            raise RuntimeError("Kubernetes resources client is not initialized")
        return await self._transport.request(operation, **arguments)

    async def create(self, manifest: dict) -> dict:
        return await self._call("create", manifest=manifest)

    async def get(self, api_version: str, kind: str, name: str) -> dict:
        return await self._call("get", api_version=api_version, kind=kind, name=name)

    async def list(self, api_version: str, kind: str, *, label_selector: str = "") -> dict:
        return await self._call(
            "list", api_version=api_version, kind=kind, label_selector=label_selector
        )

    async def delete(self, manifest: dict) -> None:
        await self._call("delete", manifest=manifest)

    async def wait_deleted(self, manifest: dict, *, timeout_s: float, interval_s: float) -> None:
        await wait_deleted(self.get, manifest, timeout_s=timeout_s, interval_s=interval_s)

    async def read_logs(self, ref: PodRef, *, container: str, max_bytes: int) -> bytes:
        if self._closed:
            raise RuntimeError("Kubernetes resources client is closed")
        if self._native is not None:
            return await self._native.read_logs(ref, container=container, max_bytes=max_bytes)
        if self._transport is None:
            raise RuntimeError("Kubernetes resources client is not initialized")
        result = await self._transport.request(
            "read_logs", ref=asdict(ref), container=container, max_bytes=max_bytes
        )
        return base64.b64decode(result["data"], validate=True)

    async def wait_completed(self, ref: PodRef, *, timeout_s: float, interval_s: float) -> Pod:
        if self._closed:
            raise RuntimeError("Kubernetes resources client is closed")
        if self._native is not None:
            return await self._native.wait_completed(
                ref, timeout_s=timeout_s, interval_s=interval_s
            )
        if self._transport is None:
            raise RuntimeError("Kubernetes resources client is not initialized")
        # The native wait owns its budget. Transport also bounds response delivery.
        result = await self._transport.request(
            "wait_completed",
            response_timeout_s=timeout_s + self._source.options.request_timeout_s,
            ref=asdict(ref),
            timeout_s=timeout_s,
            interval_s=interval_s,
        )
        return Pod(**{**result, "containers": tuple(Container(**c) for c in result["containers"])})

    async def dispose(self) -> None:
        self._closed = True
        if self._transport is not None:
            await self._transport.dispose()
