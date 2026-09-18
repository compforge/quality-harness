"""Bounded reads from the Prometheus HTTP query API.

The caller owns expressions and observation cadence. This client owns HTTP access,
response validation and one evaluation timestamp per DataLoader scope.
"""

from __future__ import annotations

import asyncio
import json
import math
import time
from collections.abc import Sequence
from dataclasses import asdict, dataclass, field
from urllib.parse import urlsplit

import httpx
from harness_common import DataSource
from harness_common.client import _ClientBorrower, client_key

from harness_toolbox.data_loader import DataLoader
from harness_toolbox.errors import ErrorKind, PrometheusQueryError
from harness_toolbox.http import HTTPClient, HTTPClientProvider


@dataclass(frozen=True)
class PrometheusQueryOptions:
    timeout_ms: int = 5_000
    max_response_bytes: int = 16 * 1024 * 1024
    max_series: int = 20_000
    connection_pool_maxsize: int = 8


@dataclass(frozen=True)
class PrometheusQueryDataSource(DataSource["PrometheusQueryClient"]):
    """Prometheus server base URL (including any proxy prefix), auth and limits."""

    url: str
    headers: dict[str, str] = field(default_factory=dict, repr=False)
    options: PrometheusQueryOptions = field(default_factory=PrometheusQueryOptions)

    def __post_init__(self) -> None:
        parts = urlsplit(self.url)
        if (
            parts.scheme not in {"http", "https"}
            or not parts.hostname
            or parts.username is not None
            or parts.query
            or parts.fragment
        ):
            raise ValueError(
                "Prometheus query URL must be an HTTP(S) base URL; use headers for auth"
            )
        if min(asdict(self.options).values()) <= 0:
            raise ValueError("Prometheus query capacity and timeout limits must be positive")

    @property
    def client_key(self) -> str:
        return client_key("prometheus_query", asdict(self))

    def create_client(self, clients: _ClientBorrower) -> PrometheusQueryClient:
        return PrometheusQueryClient(self, clients)


@dataclass(frozen=True)
class PrometheusQueryResult:
    """Native data plus server annotations; consumers decide whether warnings are acceptable."""

    data: dict
    warnings: tuple[str, ...] = ()
    infos: tuple[str, ...] = ()


class PrometheusQueryClient:
    def __init__(self, source: PrometheusQueryDataSource, clients: _ClientBorrower) -> None:
        self._source = source
        self._clients = clients
        self._http: HTTPClient | None = None

    async def initialize(self) -> None:
        options = self._source.options
        self._http = await self._clients.get(
            HTTPClientProvider(
                max_connections=options.connection_pool_maxsize,
                timeout_s=options.timeout_ms / 1000,
                pool=self._source.client_key,
            )
        )

    async def dispose(self) -> None:
        # The root ClientManager owns this borrowed pool and closes it after us.
        self._http = None

    async def read(
        self,
        expressions: Sequence[str],
        *,
        timestamp: float | None = None,
        scope: DataLoader | None = None,
    ) -> dict[str, PrometheusQueryResult]:
        """Evaluate expressions at one Unix timestamp; share identical reads within a scope.

        No selector or time window is injected into PromQL. A server-side rate
        window may include observations from before this execution started.
        """
        if self._http is None:
            raise RuntimeError("Prometheus query client is closed")
        if timestamp is None:

            async def now() -> float:
                return time.time()

            timestamp = (
                await scope.read((self._source.client_key, "evaluation_time"), now)
                if scope is not None
                else await now()
            )
        if not math.isfinite(timestamp):
            raise ValueError("Prometheus evaluation timestamp must be finite")

        async def query(expression: str) -> PrometheusQueryResult:
            async def request() -> PrometheusQueryResult:
                return await self._query(expression, timestamp)

            return (
                await scope.read((self._source.client_key, "query", expression, timestamp), request)
                if scope is not None
                else await request()
            )

        tasks = {
            expression: asyncio.create_task(query(expression))
            for expression in dict.fromkeys(expressions)
        }
        try:
            await asyncio.gather(*tasks.values())
            return {expression: task.result() for expression, task in tasks.items()}
        finally:
            # Preserve the original toolbox error while joining cancelled siblings.
            for task in tasks.values():
                if not task.done():
                    task.cancel()
            await asyncio.gather(*tasks.values(), return_exceptions=True)

    async def _query(self, expression: str, timestamp: float) -> PrometheusQueryResult:
        if self._http is None:
            raise RuntimeError("Prometheus query client is closed")
        options = self._source.options
        timeout = options.timeout_ms / 1000
        body = bytearray()
        try:
            # POST keeps query text out of URLs. The total deadline includes pool
            # acquisition and slow streaming; HTTPX's read timeout alone resets per chunk.
            async with (
                asyncio.timeout(timeout),
                self._http.stream(
                    "POST",
                    self._source.url.rstrip("/") + "/api/v1/query",
                    headers=self._source.headers,
                    data={
                        "query": expression,
                        "time": str(timestamp),
                        "timeout": f"{options.timeout_ms}ms",
                    },
                    timeout=timeout,
                ) as response,
            ):
                response.raise_for_status()
                async for chunk in response.aiter_bytes():
                    body.extend(chunk)
                    if len(body) > options.max_response_bytes:
                        raise PrometheusQueryError(
                            "Prometheus response exceeds byte limit", kind=ErrorKind.LIMIT_EXCEEDED
                        )
        except httpx.HTTPStatusError as exc:
            status = exc.response.status_code
            kind = {401: ErrorKind.AUTHENTICATION_FAILED, 403: ErrorKind.PERMISSION_DENIED}.get(
                status, ErrorKind.OPERATION_FAILED
            )
            raise PrometheusQueryError(
                f"Prometheus query HTTP request failed ({status})", kind=kind, code=status
            ) from exc
        except (TimeoutError, httpx.TimeoutException) as exc:
            raise PrometheusQueryError(
                "Prometheus query timed out", kind=ErrorKind.TIMEOUT
            ) from exc
        except httpx.RequestError as exc:
            raise PrometheusQueryError(
                "Prometheus query transport failed", kind=ErrorKind.CONNECTION_FAILED
            ) from exc
        try:
            envelope = json.loads(body)
            if not isinstance(envelope, dict):
                raise ValueError
            if envelope.get("status") == "error":
                # Server errors can echo query contents or credentials. Expose only
                # a safe protocol category; never the remote error text.
                raise PrometheusQueryError(
                    "Prometheus rejected the query", kind=ErrorKind.OPERATION_FAILED
                )
            data = envelope["data"]
            if (
                envelope.get("status") != "success"
                or not isinstance(data, dict)
                or data.get("resultType") not in {"scalar", "vector", "matrix", "string"}
                or not isinstance(data.get("result"), list)
            ):
                raise ValueError
            annotations = []
            for key in ("warnings", "infos"):
                values = envelope.get(key, [])
                if not isinstance(values, list) or not all(
                    isinstance(value, str) for value in values
                ):
                    raise ValueError
                annotations.append(tuple(values))
        except (ValueError, KeyError, TypeError) as exc:
            raise PrometheusQueryError(
                "Invalid Prometheus query response", kind=ErrorKind.INVALID_RESPONSE
            ) from exc
        # Do not send API `limit`: truncation would look like a complete observation.
        if data["resultType"] in {"vector", "matrix"} and len(data["result"]) > options.max_series:
            raise PrometheusQueryError(
                "Prometheus response exceeds series limit", kind=ErrorKind.LIMIT_EXCEEDED
            )
        return PrometheusQueryResult(data, *annotations)
