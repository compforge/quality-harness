"""Pooled OpenSearch HTTP access and bounded, explicitly closed pagination."""

from __future__ import annotations

import asyncio
import json
import ssl
from collections.abc import AsyncIterator, Mapping
from contextlib import AsyncExitStack
from dataclasses import dataclass, field
from urllib.parse import urlsplit, urlunsplit

import httpx

from harness_toolbox.client import ClientProvider, data_source_key
from harness_toolbox.transport import ConnectionSource, Endpoint, PodPythonTransport


@dataclass(frozen=True)
class OpenSearchTarget:
    url: str
    username: str = ""
    password: str = field(default="", repr=False)
    ca_file: str | None = None
    insecure_skip_verify: bool = False
    servername: str | None = None


@dataclass(frozen=True)
class OpenSearchDataSource:
    connection: ConnectionSource[OpenSearchTarget]
    timeout_s: float = 60
    concurrency: int = 8
    max_response_bytes: int = 64 * 1024 * 1024

    @property
    def key(self) -> str:
        return data_source_key(
            "opensearch",
            [
                self.connection.key,
                [t.key for t in self.connection.transports],
                self.timeout_s,
                self.concurrency,
                self.max_response_bytes,
            ],
        )

    def create_client(self, clients: ClientProvider) -> OpenSearchClient:
        return OpenSearchClient(self)


class OpenSearchClient:
    def __init__(self, source: OpenSearchDataSource) -> None:
        if source.concurrency < 1 or source.timeout_s <= 0 or source.max_response_bytes < 1:
            raise ValueError("OpenSearch limits must be positive")
        self._source = source
        self._stack = AsyncExitStack()
        self._http: httpx.AsyncClient | None = None
        self._servername: str | None = None
        self._slots = asyncio.Semaphore(source.concurrency)
        self._disposed = False
        self._disposal: asyncio.Task[None] | None = None

    async def initialize(self) -> None:
        if self._disposed:
            raise RuntimeError("OpenSearch client is disposed")
        if self._http is not None:
            return
        target = await self._source.connection.resolve()
        url = urlsplit(target.url)
        if url.scheme not in ("http", "https") or not url.hostname or url.username:
            raise ValueError("OpenSearch URL requires http(s), a host, and separate credentials")
        endpoint = Endpoint(
            url.hostname, url.port or (443 if url.scheme == "https" else 80), target.servername
        )
        if not self._source.connection.transports:
            raise ValueError("OpenSearch requires a transport")
        for index, transport in enumerate(self._source.connection.transports):
            if isinstance(transport, PodPythonTransport):
                raise ValueError("OpenSearch requires a TCP transport")
            stack = AsyncExitStack()
            try:
                mapped = await stack.enter_async_context(transport.connect(endpoint))
                host = f"[{mapped.host}]" if ":" in mapped.host else mapped.host
                base_url = urlunsplit((url.scheme, f"{host}:{mapped.port}", url.path, "", ""))
                verify: ssl.SSLContext | bool = ssl.create_default_context(cafile=target.ca_file)
                if target.insecure_skip_verify:
                    verify = False
                http = await stack.enter_async_context(
                    httpx.AsyncClient(
                        base_url=base_url,
                        auth=(target.username, target.password) if target.username else None,
                        verify=verify,
                        timeout=self._source.timeout_s,
                        trust_env=False,
                        limits=httpx.Limits(
                            max_connections=self._source.concurrency,
                            max_keepalive_connections=self._source.concurrency,
                        ),
                    )
                )
                self._servername = mapped.servername or endpoint.host
                # Probe connection only: authentication and protocol errors must not replay requests.
                response = await http.head("/", extensions={"sni_hostname": self._servername})
                response.raise_for_status()
                self._http = http
                self._stack = stack
                return
            except (httpx.ConnectError, httpx.ConnectTimeout, ConnectionError):
                await stack.aclose()
                if index + 1 == len(self._source.connection.transports):
                    raise
            except BaseException:
                await stack.aclose()
                raise

    async def request(
        self, method: str, path: str, payload: Mapping[str, object] | None = None
    ) -> dict:
        if self._http is None or self._disposed:
            raise RuntimeError("OpenSearch client is not initialized")
        if not path.startswith("/") or path.startswith("//"):
            raise ValueError("request path must be relative to the configured OpenSearch host")
        async with (
            asyncio.timeout(self._source.timeout_s),
            self._slots,
            self._http.stream(
                method, path, json=payload, extensions={"sni_hostname": self._servername}
            ) as response,
        ):
            response.raise_for_status()
            data = bytearray()
            async for chunk in response.aiter_bytes():
                data.extend(chunk)
                if len(data) > self._source.max_response_bytes:
                    raise ValueError("OpenSearch response exceeds byte limit")
            result = json.loads(data)
            if not isinstance(result, dict):
                raise ValueError("OpenSearch response must be an object")
            return result

    async def scroll(
        self,
        index: str,
        query: Mapping[str, object],
        *,
        page_size: int = 100,
        keep_alive: str = "2m",
    ) -> AsyncIterator[list[dict]]:
        """Use contextlib.aclosing when stopping before exhaustion to release the scroll."""
        if page_size < 1 or "/" in index or "?" in index or "#" in index:
            raise ValueError("invalid index or page size")
        scroll_id: str | None = None
        try:
            result = await self.request(
                "POST",
                f"/{index}/_search?scroll={keep_alive}",
                {"query": query, "size": page_size, "sort": ["_doc"]},
            )
            while True:
                scroll_id = result.get("_scroll_id", scroll_id)
                hits = result.get("hits", {}).get("hits", [])
                if not hits:
                    break
                yield hits
                if not scroll_id:
                    raise ValueError("OpenSearch returned hits without a scroll id")
                result = await self.request(
                    "POST", "/_search/scroll", {"scroll": keep_alive, "scroll_id": scroll_id}
                )
        finally:
            if scroll_id:
                await self.request("DELETE", "/_search/scroll", {"scroll_id": [scroll_id]})

    async def dispose(self) -> None:
        if self._disposal is None:
            self._disposed = True
            self._disposal = asyncio.create_task(self._dispose())
        await asyncio.shield(self._disposal)

    async def _dispose(self) -> None:
        await self._stack.aclose()
        self._http = None
