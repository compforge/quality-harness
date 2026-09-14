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
from harness_toolbox.diagnostics import _AccessRecorder, _transport_name
from harness_toolbox.errors import (
    ErrorKind,
    OpenSearchConnectionError,
    OpenSearchError,
    OpenSearchRequestError,
    ToolboxError,
)
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


def _tls_verification_failed(error: BaseException) -> bool:
    # HTTP backends can wrap SSL errors or retain only the OpenSSL marker.
    seen: set[int] = set()
    current: BaseException | None = error
    while current is not None and id(current) not in seen and len(seen) < 16:
        seen.add(id(current))
        if isinstance(current, ssl.SSLCertVerificationError) or any(
            isinstance(arg, str) and "CERTIFICATE_VERIFY_FAILED" in arg for arg in current.args
        ):
            return True
        current = current.__cause__ or (
            None if current.__suppress_context__ else current.__context__
        )
    return False


def _failure(
    endpoint: Endpoint,
    error_type: type[OpenSearchError],
    kind: ErrorKind,
    code: int | None = None,
) -> OpenSearchError:
    action = "connect" if error_type is OpenSearchConnectionError else "request"
    return error_type(
        f"OpenSearch {action} failed at {endpoint.host}:{endpoint.port}: "
        f"{kind.value.replace('_', ' ')}",
        kind=kind,
        code=code,
    )


def _opensearch_error(
    error: Exception, endpoint: Endpoint, error_type: type[OpenSearchError]
) -> ToolboxError | None:
    if isinstance(error, ToolboxError):
        return error
    code = None
    if isinstance(error, (httpx.TransportError, ssl.SSLError)) and _tls_verification_failed(error):
        kind = ErrorKind.TLS_VERIFICATION_FAILED
    elif isinstance(error, httpx.HTTPStatusError):
        code = error.response.status_code
        kind = {
            401: ErrorKind.AUTHENTICATION_FAILED,
            403: ErrorKind.PERMISSION_DENIED,
            404: ErrorKind.RESOURCE_NOT_FOUND,
        }.get(code, ErrorKind.OPERATION_FAILED)
    elif isinstance(error, (TimeoutError, httpx.TimeoutException)):
        kind = ErrorKind.TIMEOUT
    elif isinstance(error, (httpx.ConnectError, ConnectionError)):
        kind = ErrorKind.CONNECTION_FAILED
    elif isinstance(error, (httpx.ReadError, httpx.WriteError)):
        kind = ErrorKind.CONNECTION_LOST
    elif isinstance(error, (httpx.DecodingError, httpx.RemoteProtocolError)):
        kind = ErrorKind.INVALID_RESPONSE
    elif isinstance(error, (httpx.HTTPError, OSError)):
        kind = ErrorKind.OPERATION_FAILED
    else:
        return None
    return _failure(endpoint, error_type, kind, code)


class OpenSearchClient:
    def __init__(self, source: OpenSearchDataSource) -> None:
        if source.concurrency < 1 or source.timeout_s <= 0 or source.max_response_bytes < 1:
            raise ValueError("OpenSearch limits must be positive")
        self._source = source
        self._access = _AccessRecorder("opensearch")
        self._stack = AsyncExitStack()
        self._http: httpx.AsyncClient | None = None
        self._servername: str | None = None
        self._endpoint: Endpoint | None = None
        self._slots = asyncio.Semaphore(source.concurrency)
        self._disposed = False
        self._disposal: asyncio.Task[None] | None = None

    @property
    def diagnostics(self) -> dict:
        """Resolved target and attempted/selected transports, without URL payloads."""
        return self._access.snapshot()

    async def initialize(self) -> None:
        if self._disposed:
            raise RuntimeError("OpenSearch client is disposed")
        if self._http is not None:
            return
        self._access = _AccessRecorder("opensearch")
        target = await self._source.connection.resolve()
        url = urlsplit(target.url)
        if url.scheme not in ("http", "https") or not url.hostname or url.username:
            raise ValueError("OpenSearch URL requires http(s), a host, and separate credentials")
        endpoint = Endpoint(
            url.hostname, url.port or (443 if url.scheme == "https" else 80), target.servername
        )
        self._endpoint = endpoint
        self._access.target = {
            "host": endpoint.host,
            "port": endpoint.port,
            "servername": endpoint.servername or endpoint.host,
        }
        if not self._source.connection.transports:
            raise ValueError("OpenSearch requires a transport")
        for index, transport in enumerate(self._source.connection.transports):
            if isinstance(transport, PodPythonTransport):
                raise ValueError("OpenSearch requires a TCP transport")
            stack = AsyncExitStack()
            try:
                try:
                    mapped = await stack.enter_async_context(transport.connect(endpoint))
                except RuntimeError as error:
                    raise _failure(
                        endpoint, OpenSearchConnectionError, ErrorKind.OPERATION_FAILED
                    ) from error
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
                self._access.connected(_transport_name(transport))
                return
            except BaseException as error:
                failure = (
                    _opensearch_error(error, endpoint, OpenSearchConnectionError)
                    if isinstance(error, Exception)
                    else None
                )
                if failure is not None:
                    self._access.failed(_transport_name(transport), failure)
                await stack.aclose()
                if failure is None or failure is error:
                    raise
                if isinstance(
                    error, (httpx.ConnectError, httpx.ConnectTimeout, ConnectionError)
                ) and index + 1 < len(self._source.connection.transports):
                    continue
                raise failure from error

    async def request(
        self, method: str, path: str, payload: Mapping[str, object] | None = None
    ) -> dict:
        if self._http is None or self._disposed:
            raise RuntimeError("OpenSearch client is not initialized")
        if not path.startswith("/") or path.startswith("//"):
            raise ValueError("request path must be relative to the configured OpenSearch host")
        try:
            return await self._request(method, path, payload)
        except Exception as error:
            assert self._endpoint is not None
            failure = _opensearch_error(error, self._endpoint, OpenSearchRequestError)
            if failure is None or failure is error:
                raise
            raise failure from error

    async def _request(self, method: str, path: str, payload: Mapping[str, object] | None) -> dict:
        assert self._endpoint is not None
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
                    raise _failure(self._endpoint, OpenSearchRequestError, ErrorKind.LIMIT_EXCEEDED)
            try:
                result = json.loads(data)
            except (ValueError, UnicodeError) as error:
                raise _failure(
                    self._endpoint, OpenSearchRequestError, ErrorKind.INVALID_RESPONSE
                ) from error
            if not isinstance(result, dict):
                raise _failure(self._endpoint, OpenSearchRequestError, ErrorKind.INVALID_RESPONSE)
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
                    assert self._endpoint is not None
                    raise _failure(
                        self._endpoint, OpenSearchRequestError, ErrorKind.INVALID_RESPONSE
                    )
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
