"""Native manifest operations sharing the Kubernetes client's connection pool."""

from __future__ import annotations

import asyncio
import json
from collections.abc import Callable
from typing import Any

from aiohttp import ClientResponse
from kubernetes_asyncio.client import ApiClient, ApiException
from kubernetes_asyncio.dynamic import DynamicClient, Resource

from harness_toolbox.kube.model import Options


class KubernetesResources:
    """Raw Kubernetes manifests without product schemas or lifecycle adoption.

    Namespaced resources are confined to the owning client's namespace. Cluster
    resources require explicit apiVersion/kind and the caller's API permissions.
    Every create runs once; delete requires the observed object's UID.
    """

    def __init__(self, api: Callable[[], ApiClient], options: Options) -> None:
        self._api = api
        self._options = options
        self._dynamic: DynamicClient | None = None
        self._lock = asyncio.Lock()

    async def _resource(self, api_version: str, kind: str) -> Resource:
        api = self._api()  # Borrowed operations cannot outlive ClientManager.
        async with self._lock:
            if self._dynamic is None:
                dynamic = DynamicClient(api)
                await dynamic.__aenter__()
                self._dynamic = dynamic
        return await self._dynamic.resources.get(api_version=api_version, kind=kind)

    def _namespace(self, resource: Resource) -> str | None:
        return self._options.namespace if resource.namespaced else None

    async def create(self, manifest: dict[str, Any]) -> dict[str, Any]:
        async with asyncio.timeout(self._options.request_timeout_s):
            resource = await self._resource(manifest["apiVersion"], manifest["kind"])
            namespace = self._namespace(resource)
            declared = manifest.get("metadata", {}).get("namespace")
            if declared and declared != namespace:
                raise ValueError("manifest namespace differs from the Kubernetes client")
            result = await resource.create(
                body=manifest,
                namespace=namespace,
                _request_timeout=self._options.request_timeout_s,
                serialize=False,
            )
            return await _decode(result)

    async def get(self, api_version: str, kind: str, name: str) -> dict[str, Any]:
        async with asyncio.timeout(self._options.request_timeout_s):
            resource = await self._resource(api_version, kind)
            result = await resource.get(
                name=name,
                namespace=self._namespace(resource),
                _request_timeout=self._options.request_timeout_s,
                serialize=False,
            )
            return await _decode(result)

    async def list(
        self, api_version: str, kind: str, *, label_selector: str = ""
    ) -> dict[str, Any]:
        async with asyncio.timeout(self._options.request_timeout_s):
            resource = await self._resource(api_version, kind)
            result = await resource.get(
                namespace=self._namespace(resource),
                label_selector=label_selector,
                _request_timeout=self._options.request_timeout_s,
                serialize=False,
            )
            return await _decode(result)

    async def delete(self, manifest: dict[str, Any]) -> None:
        """Delete only the observed instance; 409 means the name was replaced."""
        metadata = manifest["metadata"]
        if not metadata.get("uid") or not metadata.get("name"):
            raise ValueError("delete requires resource name and UID")
        async with asyncio.timeout(self._options.request_timeout_s):
            resource = await self._resource(manifest["apiVersion"], manifest["kind"])
            namespace = self._namespace(resource)
            if resource.namespaced and metadata.get("namespace") != namespace:
                raise ValueError("manifest namespace differs from the Kubernetes client")
            result = await resource.delete(
                name=metadata["name"],
                namespace=namespace,
                body={
                    "apiVersion": "v1",
                    "kind": "DeleteOptions",
                    "preconditions": {"uid": metadata["uid"]},
                    "propagationPolicy": "Background",
                },
                _request_timeout=self._options.request_timeout_s,
                serialize=False,
            )
            await _decode(result)

    async def wait_deleted(
        self, manifest: dict[str, Any], *, timeout_s: float, interval_s: float
    ) -> None:
        await wait_deleted(self.get, manifest, timeout_s=timeout_s, interval_s=interval_s)


async def wait_deleted(
    get: Callable, manifest: dict, *, timeout_s: float, interval_s: float
) -> None:
    metadata = manifest["metadata"]
    if not metadata.get("uid") or min(timeout_s, interval_s) <= 0:
        raise ValueError("resource UID and positive wait budgets are required")
    async with asyncio.timeout(timeout_s):
        while True:
            try:
                current = await get(manifest["apiVersion"], manifest["kind"], metadata["name"])
            except ApiException as error:
                if error.status == 404:
                    return
                raise
            if current["metadata"]["uid"] != metadata["uid"]:
                return
            await asyncio.sleep(interval_s)


async def _decode(response: ClientResponse) -> dict[str, Any]:
    # DynamicClient uses non-preloaded responses: status must be checked before
    # interpreting a Kubernetes Status error object as a created resource.
    try:
        body = await response.text()
        if not 200 <= response.status < 300:
            error = ApiException(status=response.status, reason=response.reason)
            error.body = body
            raise error
        return json.loads(body)
    finally:
        response.release()
