"""Bounded Prometheus scraping and PromQL reads through an embedded Prombed."""

from __future__ import annotations

import asyncio
from collections import deque
from collections.abc import Sequence
from dataclasses import asdict, dataclass, field
from typing import TYPE_CHECKING

import httpx
from harness_common import DataSource
from harness_common.client import _ClientBorrower, client_key
from prombed import Prombed, PrombedError, ScrapeTarget
from prombed.types import LabelMatcher, Sample

from harness_toolbox.data_loader import DataLoader

if TYPE_CHECKING:
    from harness_toolbox.prometheus_discovery import KubernetesScrapeDiscovery


@dataclass(frozen=True)
class PrometheusOptions:
    timeout_ms: int = 5_000
    max_scrape_bytes: int = 16 * 1024 * 1024
    retention_ms: int = 10 * 60_000
    max_series: int = 20_000
    max_samples_per_series: int = 10_000
    connection_pool_maxsize: int = 8


@dataclass(frozen=True)
class PrometheusDataSource(DataSource["PrometheusClient"]):
    """Endpoint, explicit credentials and capacity; logical service names stay outside."""

    url: str = ""
    headers: dict[str, str] = field(default_factory=dict, repr=False)
    options: PrometheusOptions = field(default_factory=PrometheusOptions)

    targets: tuple[ScrapeTarget, ...] = field(default=(), repr=False)
    discovery: KubernetesScrapeDiscovery | None = None

    @property
    def client_key(self) -> str:
        return client_key("prometheus", asdict(self))

    def create_client(self, clients: _ClientBorrower) -> PrometheusClient:
        return PrometheusClient(self, clients=clients)


class PrometheusClient:
    """ClientManager owns the HTTP pool and the lifetime of retained observations.

    Each read scrapes once, then evaluates all expressions at that scrape's time.
    A DataLoader shares that scrape across readers of the same source. Query
    responses retain Prometheus labels/types; interpretation belongs to consumers.
    """

    def __init__(
        self,
        source: PrometheusDataSource,
        *,
        transport: httpx.AsyncBaseTransport | None = None,
        clients: _ClientBorrower | None = None,
    ) -> None:
        self._source = source
        self._clients = clients
        self._slots = asyncio.Semaphore(source.options.connection_pool_maxsize)
        self._scrapes: deque[int] = deque(maxlen=source.options.max_samples_per_series)
        self._first_scrape: int | None = None
        self._instances: set[str] = set()
        self._failed = False
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
            retention_ms=options.retention_ms,
            max_series=options.max_series,
            max_samples_per_series=options.max_samples_per_series,
            scrape_timeout_ms=options.timeout_ms,
            max_scrape_bytes=options.max_scrape_bytes,
            fetch=self._fetch,
        )

    async def _fetch_body(
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

    async def _fetch(
        self, url: str, headers: dict[str, str], timeout: float, max_bytes: int
    ) -> bytes:
        # The deadline includes waiting for the shared scrape pool.
        async with asyncio.timeout(timeout), self._slots:
            return await self._fetch_body(url, headers, timeout, max_bytes)

    async def _targets(self) -> list[ScrapeTarget]:
        source = self._source
        modes = bool(source.url) + bool(source.targets) + (source.discovery is not None)
        if modes != 1:
            raise ValueError("Configure exactly one metrics URL, target list or workload discovery")
        if source.discovery is not None:
            if self._clients is None:
                raise RuntimeError("Workload discovery requires a ClientManager")
            targets = await source.discovery.resolve(self._clients)
        else:
            targets = list(source.targets) if source.targets else [ScrapeTarget(source.url)]
        if not targets:
            raise ValueError("Metrics discovery returned no running targets")
        return [
            ScrapeTarget(
                target.url,
                labels={"instance": target.url, **target.labels},
                headers={**source.headers, **target.headers},
                timeout_ms=source.options.timeout_ms,
                max_body_bytes=source.options.max_scrape_bytes,
            )
            for target in targets
        ]

    async def _scrape(self) -> int:
        try:
            async with asyncio.timeout(self._source.options.timeout_ms / 1000):
                return await self._collect()
        except TimeoutError as exc:
            self._failed = True
            raise PrombedError("scrape_failed", "Metric scrape exceeded total timeout") from exc
        except BaseException:
            self._failed = True
            raise

    async def _collect(self) -> int:
        async with self._lock:
            if self._runtime is None or self._closed:
                raise RuntimeError("Prometheus client is closed")
            tasks: list[asyncio.Task] = []
            try:
                targets = await self._targets()
                instances = {target.labels["instance"] for target in targets}
                if len(instances) != len(targets):
                    raise ValueError("Metric targets must have unique instance identities")
                # Wait for every target, including failed ones. A partial fleet is
                # not a successful scrape and cancellation cannot leave sibling I/O.
                tasks = [
                    asyncio.create_task(self._runtime.scrape_once(target)) for target in targets
                ]
                results = await asyncio.gather(*tasks, return_exceptions=True)
                failures = [result for result in results if isinstance(result, BaseException)]
                if failures:
                    raise failures[0]
                timestamp = max(result[0].scraped_at for result in results)
                removed = self._instances - instances
                if removed:
                    appender = self._runtime.storage.appender(timestamp)
                    try:
                        for instance in removed:
                            for series in self._runtime.storage.select(
                                [LabelMatcher("instance", "=", instance)]
                            ):
                                appender.append(series.labels, Sample(timestamp, 0.0, stale=True))
                        appender.commit()
                    except BaseException:
                        appender.rollback()
                        raise
                self._instances = instances
                if self._first_scrape is None:
                    self._first_scrape = min(result[0].scraped_at for result in results)
                self._scrapes.append(timestamp)
                return timestamp
            finally:
                for task in tasks:
                    if not task.done():
                        task.cancel()
                if tasks:
                    await asyncio.gather(*tasks, return_exceptions=True)

    @property
    def history_bounds(self) -> tuple[int, int]:
        if self._first_scrape is None or not self._scrapes:
            raise ValueError("No successful metric scrapes")
        return self._first_scrape, self._scrapes[-1]

    def query_window(
        self, expressions: Sequence[str], *, start_ms: int, end_ms: int
    ) -> dict[str, dict]:
        """Query retained samples only; never scrape or access the service.

        Reject lost history or incomplete target coverage rather than reporting a
        seemingly complete window. PromQL owns reset correction and aggregation.
        """
        if self._runtime is None or self._closed:
            raise RuntimeError("Prometheus client is closed")
        first, last = self.history_bounds
        options = self._source.options
        retained = max(first, last - options.retention_ms)
        if len(self._scrapes) == options.max_samples_per_series:
            retained = max(retained, self._scrapes[-options.max_samples_per_series])
        if self._failed:
            raise ValueError("Window metrics unavailable: one or more target scrapes failed")
        if start_ms < retained or end_ms > last or start_ms >= end_ms:
            raise ValueError("Window metrics unavailable: requested history is not retained")
        return {
            expression: self._runtime.query(expression, end_ms)["data"]
            for expression in expressions
        }

    async def read(
        self, expressions: Sequence[str], *, scope: DataLoader | None = None
    ) -> dict[str, dict]:
        if self._runtime is None or self._closed:
            raise RuntimeError("Prometheus client is closed")
        timestamp = (
            await scope.read((self._source.client_key, "scrape"), self._scrape)
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
