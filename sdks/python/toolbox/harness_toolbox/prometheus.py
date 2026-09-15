"""Bounded Prometheus scraping and PromQL reads through an embedded Prombed."""

from __future__ import annotations

import asyncio
from collections.abc import Sequence
from dataclasses import asdict, dataclass, field

import httpx
from harness_common.client import ClientProvider, data_source_key
from prombed import Prombed, ScrapeTarget

from harness_toolbox.data_loader import DataLoader


@dataclass(frozen=True)
class PrometheusOptions:
    timeout_ms: int = 5_000
    max_scrape_bytes: int = 16 * 1024 * 1024
    retention_ms: int = 10 * 60_000
    max_series: int = 20_000
    max_samples_per_series: int = 10_000
    connection_pool_maxsize: int = 8


@dataclass(frozen=True)
class PrometheusDataSource:
    """Endpoint, explicit credentials and capacity; logical service names stay outside."""

    url: str
    headers: dict[str, str] = field(default_factory=dict, repr=False)
    options: PrometheusOptions = field(default_factory=PrometheusOptions)

    @property
    def key(self) -> str:
        return data_source_key("prometheus", asdict(self))

    def create_client(self, clients: ClientProvider) -> PrometheusClient:
        return PrometheusClient(self)


class PrometheusClient:
    """ClientManager owns the HTTP pool and the lifetime of retained observations.

    Each read scrapes once, then evaluates all expressions at that scrape's time.
    A DataLoader shares that scrape across readers of the same source. Query
    responses retain Prometheus labels/types; interpretation belongs to consumers.
    """

    def __init__(
        self, source: PrometheusDataSource, *, transport: httpx.AsyncBaseTransport | None = None
    ) -> None:
        self._source = source
        self._transport = transport
        self._http: httpx.AsyncClient | None = None
        self._runtime: Prombed | None = None
        self._lock = asyncio.Lock()
        self._closed = False

    async def initialize(self) -> None:
        options = self._source.options
        if min(asdict(options).values()) <= 0:
            raise ValueError("Prometheus capacity and timeout limits must be positive")
        self._http = httpx.AsyncClient(
            limits=httpx.Limits(
                max_connections=options.connection_pool_maxsize,
                max_keepalive_connections=options.connection_pool_maxsize,
            ),
            trust_env=False,
            transport=self._transport,
        )
        self._runtime = Prombed(
            targets=[
                ScrapeTarget(
                    self._source.url,
                    headers=self._source.headers,
                    timeout_ms=options.timeout_ms,
                    max_body_bytes=options.max_scrape_bytes,
                )
            ],
            retention_ms=options.retention_ms,
            max_series=options.max_series,
            max_samples_per_series=options.max_samples_per_series,
            scrape_timeout_ms=options.timeout_ms,
            max_scrape_bytes=options.max_scrape_bytes,
            fetch=self._fetch,
        )

    async def _fetch(
        self, url: str, headers: dict[str, str], timeout: float, max_bytes: int
    ) -> bytes:
        if self._http is None or self._closed:
            raise RuntimeError("Prometheus client is closed")
        body = bytearray()
        # HTTPX read timeouts reset per chunk; also bound the whole scrape.
        async with (
            asyncio.timeout(timeout),
            self._http.stream("GET", url, headers=headers, timeout=timeout) as response,
        ):
            response.raise_for_status()
            async for chunk in response.aiter_bytes():
                body.extend(chunk)
                if len(body) > max_bytes:
                    raise ValueError(
                        f"Prometheus response exceeds configured limit of {max_bytes} bytes"
                    )
        return bytes(body)

    async def _scrape(self) -> int:
        async with self._lock:
            if self._runtime is None or self._closed:
                raise RuntimeError("Prometheus client is closed")
            result = (await self._runtime.scrape_once())[0]
            return result.scraped_at

    async def read(
        self, expressions: Sequence[str], *, scope: DataLoader | None = None
    ) -> dict[str, dict]:
        if self._runtime is None or self._closed:
            raise RuntimeError("Prometheus client is closed")
        timestamp = (
            await scope.read((self._source.key, "scrape"), self._scrape)
            if scope is not None
            else await self._scrape()
        )
        if self._runtime is None or self._closed:
            raise RuntimeError("Prometheus client is closed")
        return {
            expression: self._runtime.query(expression, timestamp)["data"]
            for expression in expressions
        }

    async def dispose(self) -> None:
        self._closed = True
        if self._http is not None:
            await self._http.aclose()
        self._runtime = None
