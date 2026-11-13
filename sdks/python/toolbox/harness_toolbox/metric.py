"""Bounded Prometheus scraping and PromQL reads through an embedded Prombed."""

from __future__ import annotations

import asyncio
import logging
import time
from collections import deque
from collections.abc import Sequence
from dataclasses import asdict, dataclass, field, replace
from typing import Literal
from urllib.parse import urlsplit

import httpx
from harness_common import DataSource, KubernetesWorkload
from harness_common.access.client import _ClientBorrower, client_key
from prombed import Prombed, PrombedError, ScrapeTarget
from prombed.types import LabelMatcher, Sample

from harness_toolbox.data_loader import DataLoader
from harness_toolbox.environment import KubernetesEnvironment
from harness_toolbox.http import HTTPClient, HTTPClientProvider
from harness_toolbox.transport import KubernetesAccess, KubernetesPortForwardTransport

LOG = logging.getLogger(__name__)


@dataclass(frozen=True)
class MetricOptions:
    timeout_ms: int = 5_000
    max_scrape_bytes: int = 16 * 1024 * 1024
    retention_ms: int = 10 * 60_000
    max_series: int = 20_000
    max_samples_per_series: int = 10_000
    connection_pool_maxsize: int = 8
    prepare_timeout_ms: int = 30_000


@dataclass(frozen=True)
class MetricDataSource(DataSource["MetricClient"]):
    """Explicit metrics endpoints or declared Workloads, auth, route and budgets.

    @spec Workload snapshots become UID-bound scrape targets on each read cycle.
    @rule A partial fleet retains healthy samples but cannot produce a complete aggregate.
    @link component://docs/toolbox.md
    """

    kind: str = field(default="prombed", init=False)

    url: str = ""
    headers: dict[str, str] = field(default_factory=dict, repr=False)
    options: MetricOptions = field(default_factory=MetricOptions)

    targets: tuple[ScrapeTarget, ...] = field(default=(), repr=False)
    environment: KubernetesEnvironment | None = None
    workloads: tuple[KubernetesWorkload, ...] = ()
    port: int = 8000
    path: str = "/metrics"
    scheme: str = "http"
    transport: Literal["direct", "port-forward"] = "direct"
    metric_names: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if bool(self.url) + bool(self.targets) + bool(self.workloads) != 1:
            raise ValueError("Configure exactly one metrics URL, target list or workloads")
        if self.workloads and self.environment is None:
            raise ValueError("Metric workloads require a Kubernetes environment")
        if self.transport not in {"direct", "port-forward"}:
            raise ValueError("Unknown metric transport")
        if self.transport == "port-forward" and not self.workloads:
            raise ValueError("Metric port-forward requires explicit workloads")
        if not 0 < self.port < 65536 or self.scheme not in {"http", "https"}:
            raise ValueError("Invalid metrics port or scheme")
        if not self.path.startswith("/") or urlsplit(self.path).netloc:
            raise ValueError("Invalid metrics path")
        if not isinstance(self.metric_names, tuple) or any(
            not isinstance(name, str) or not name for name in self.metric_names
        ):
            raise ValueError("metric_names must be a tuple of nonempty metric names")

    @property
    def client_key(self) -> str:
        return client_key("metric", asdict(self))

    def create_client(self, clients: _ClientBorrower) -> MetricClient:
        return MetricClient(self, clients=clients)


class MetricClient:
    """ClientManager owns the HTTP pool and the lifetime of retained observations.

    Each read scrapes once, then evaluates all expressions at that scrape's time.
    A DataLoader shares that scrape across readers of the same source. Query
    responses retain Prometheus labels/types; interpretation belongs to consumers.
    """

    def __init__(
        self,
        source: MetricDataSource,
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
        self._pools: dict[str, HTTPClient] = {}
        self._diagnostics: dict[str, dict] = {}

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

    @property
    def diagnostics(self) -> dict[str, dict]:
        """Detached last-cycle target evidence, including failed and incomplete reads."""
        from copy import deepcopy

        return deepcopy(self._diagnostics)

    async def _fetch_body(
        self, url: str, headers: dict[str, str], timeout: float, max_bytes: int
    ) -> bytes:
        if self._http is None or self._closed:
            raise RuntimeError("Metric client is closed")
        http = self._pools.get(url, self._http)
        row = self._diagnostics[url]

        started = time.monotonic()
        row["stage"] = "headers"
        body = bytearray()
        async with (
            asyncio.timeout(timeout),
            http.stream("GET", url, headers=headers, timeout=timeout) as response,
        ):
            row["ttfb_s"] = time.monotonic() - started
            row["status_code"] = response.status_code
            row["content_encoding"] = response.headers.get("content-encoding", "identity")
            response.raise_for_status()
            row["stage"] = "body"
            async for chunk in response.aiter_bytes():
                body.extend(chunk)
                row["decoded_bytes"] = len(body)
                row["wire_bytes"] = response.num_bytes_downloaded
                if len(body) > max_bytes:
                    raise ValueError(
                        f"Metrics response exceeds configured limit of {max_bytes} bytes"
                    )
        row["http_s"] = time.monotonic() - started
        row["stage"] = "parse"
        return bytes(body)

    async def _fetch(
        self, url: str, headers: dict[str, str], timeout: float, max_bytes: int
    ) -> bytes:
        async with asyncio.timeout(timeout), self._slots:
            return await self._fetch_body(url, headers, timeout, max_bytes)

    async def _workload_targets(self, scope: DataLoader | None) -> list[ScrapeTarget]:
        source = self._source
        if self._clients is None or source.environment is None:
            raise RuntimeError("Metric workloads require a ClientManager and environment")
        targets: dict[str, ScrapeTarget] = {}
        self._pools = {}
        for workload in source.workloads:
            environment = replace(
                source.environment,
                options=replace(
                    source.environment.options,
                    namespace=workload.namespace or source.environment.options.namespace,
                ),
            )
            resources = await self._clients.get(environment)

            async def resolve(resources=resources, workload=workload, environment=environment):
                return await resources.resolve_workload(workload, environment=environment.name)

            instances = (
                await scope.read(
                    (environment.client_key, environment.name, "workload", repr(workload)), resolve
                )
                if scope
                else await resolve()
            )
            for instance in instances:
                pod = await resources.get("v1", "Pod", instance.pod)
                identity = f"{instance.namespace}/{instance.uid}"
                if pod["metadata"]["uid"] != instance.uid:
                    raise ValueError(f"Metrics target changed: {identity}")
                status = pod.get("status", {})
                if (
                    pod["metadata"].get("deletionTimestamp")
                    or status.get("phase") != "Running"
                    or not status.get("podIP")
                ):
                    self._diagnostics[identity] = {
                        "instance": identity,
                        "stage": "prepare",
                        "status": "unavailable",
                    }
                    continue
                address = status["podIP"]
                host = f"[{address}]" if ":" in address else address
                url = f"{source.scheme}://{host}:{source.port}{source.path}"
                if source.transport == "port-forward":
                    if environment.host and environment.host.transport != "local":
                        raise ValueError("Port-forward must execute on the Kubernetes access host")
                    route_source = KubernetesPortForwardTransport(
                        KubernetesAccess(
                            environment.kubeconfig or None, instance.namespace, environment.context
                        ),
                        timeout_s=source.options.prepare_timeout_ms / 1000,
                    )
                    route = await self._clients.get(route_source)
                    endpoint = await route.pod_endpoint(instance.pod, instance.uid, source.port)
                    # Prepare once; subsequent sockets reuse the UID-bound tunnel.
                    async with route.connect(endpoint):
                        pass
                    self._pools[url] = await self._clients.get(
                        HTTPClientProvider(
                            source.options.connection_pool_maxsize,
                            source.options.timeout_ms / 1000,
                            pool=f"{source.client_key}:{identity}",
                            transport=route_source,
                        )
                    )
                targets[identity] = ScrapeTarget(url, labels={"instance": identity})
        return [targets[key] for key in sorted(targets)]

    async def _targets(self, scope: DataLoader | None = None) -> list[ScrapeTarget]:
        source = self._source
        targets = (
            await self._workload_targets(scope)
            if source.workloads
            else list(source.targets)
            if source.targets
            else [ScrapeTarget(source.url)]
        )
        if not targets:
            raise ValueError("Metrics workloads returned no running targets")
        return [
            ScrapeTarget(
                target.url,
                labels={"instance": target.url, **target.labels},
                headers={**source.headers, **target.headers},
                timeout_ms=source.options.timeout_ms,
                max_body_bytes=source.options.max_scrape_bytes,
                metric_names=source.metric_names or target.metric_names,
            )
            for target in targets
        ]

    async def _scrape(self, scope: DataLoader | None = None) -> int:

        async with self._lock:
            self._diagnostics = {}
            started = time.monotonic()
            try:
                async with asyncio.timeout(self._source.options.prepare_timeout_ms / 1000):
                    targets = await self._targets(scope)
            except BaseException:
                self._failed = True
                self._diagnostics["prepare"] = {
                    "stage": "prepare",
                    "status": "error",
                    "elapsed_s": time.monotonic() - started,
                }
                raise
            self._prepare_s = time.monotonic() - started
            tasks = []
            try:
                async with asyncio.timeout(self._source.options.timeout_ms / 1000):
                    return await self._collect(targets, tasks)
            except TimeoutError as exc:
                self._failed = True
                failed = [row for row in self._diagnostics.values() if row.get("status") != "ok"]
                detail = ", ".join(f"{r.get('instance', '?')}:{r['stage']}" for r in failed)
                raise PrombedError(
                    "scrape_failed", f"Metric scrape exceeded total timeout ({detail})"
                ) from exc
            except BaseException:
                self._failed = True
                raise
            finally:
                for task in tasks:
                    if not task.done():
                        task.cancel()
                if tasks:
                    await asyncio.gather(*tasks, return_exceptions=True)

    async def _collect(self, targets: list[ScrapeTarget], tasks: list[asyncio.Task]) -> int:

        if self._runtime is None or self._closed:
            raise RuntimeError("Metric client is closed")
        instances = {target.labels["instance"] for target in targets}
        if len(instances) != len(targets):
            raise ValueError("Metric targets must have unique instance identities")

        async def collect(target):
            row = {
                "instance": target.labels["instance"],
                "transport": self._source.transport,
                "stage": "queue",
                "status": "pending",
                "decoded_bytes": 0,
                "prepare_s": self._prepare_s,
            }
            self._diagnostics[target.url] = row
            started = time.monotonic()
            try:
                result = await self._runtime.scrape_once(target)
                row.update(stage="complete", status="ok")
                row["processing_s"] = time.monotonic() - started - row.get("http_s", 0)
                return result
            except BaseException as exc:
                row["status"] = "cancelled" if isinstance(exc, asyncio.CancelledError) else "error"
                row["error_type"] = type(exc).__name__
                LOG.warning(
                    "metric target failed: instance=%s transport=%s stage=%s bytes=%s error=%s",
                    row["instance"],
                    row["transport"],
                    row["stage"],
                    row["decoded_bytes"],
                    row["error_type"],
                )
                raise
            finally:
                row["elapsed_s"] = time.monotonic() - started

        tasks.extend(asyncio.create_task(collect(target)) for target in targets)
        results = await asyncio.gather(*tasks, return_exceptions=True)
        failures = [result for result in results if isinstance(result, BaseException)]
        if failures or any(r.get("status") == "unavailable" for r in self._diagnostics.values()):
            self._failed = True
            if failures:
                raise failures[0]
            raise PrombedError("scrape_failed", "Metrics workload contains unavailable instances")
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

        async def scrape():
            return await self._scrape(scope)

        timestamp = (
            await scope.read((self._source.client_key, "scrape"), scrape)
            if scope is not None
            else await scrape()
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
