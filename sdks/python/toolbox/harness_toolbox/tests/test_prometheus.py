import asyncio
from dataclasses import replace
from itertools import count

import httpx
import pytest
from harness_common.client import ClientManager
from prombed import PrombedError

from harness_toolbox.data_loader import DataLoader
from harness_toolbox.prometheus import PrometheusClient, PrometheusDataSource, PrometheusOptions


@pytest.fixture(autouse=True)
def scrape_clock(monkeypatch):
    # Separate observation cycles even when MockTransport completes in one millisecond.
    clock = count(1_000_000, 10)
    monkeypatch.setattr("prombed.prombed._now_ms", lambda: next(clock))


def with_transport(monkeypatch, handler):
    monkeypatch.setattr(
        PrometheusDataSource,
        "create_client",
        lambda source, _: PrometheusClient(source, transport=httpx.MockTransport(handler)),
    )


async def test_datasource_shares_client_and_scoped_scrape_but_preserves_raw_labels(monkeypatch):
    calls = []

    def handler(request):
        calls.append(request)
        return httpx.Response(200, text='requests_total{path="/chat"} 7\n')

    with_transport(monkeypatch, handler)
    source = PrometheusDataSource("http://metrics/metrics", headers={"Authorization": "secret"})
    assert "secret" not in repr(source)
    async with ClientManager() as clients:
        client = await clients.get(source)
        assert client is await clients.get(replace(source))
        assert client is not await clients.get(replace(source, headers={"Authorization": "other"}))
        assert client is not await clients.get(
            replace(source, options=PrometheusOptions(max_series=10))
        )
        async with DataLoader() as scope:
            a, b = await asyncio.gather(
                client.read(["requests_total"], scope=scope),
                client.read(["sum(requests_total)", "1 + 2"], scope=scope),
            )
        assert len(calls) == 1
        assert calls[0].headers["Authorization"] == "secret"
        row = a["requests_total"]["result"][0]
        assert row["metric"]["path"] == "/chat"
        assert "instance" in row["metric"]
        assert float(row["value"][1]) == 7
        assert float(b["1 + 2"]["result"][1]) == 3
        async with DataLoader() as scope:
            await client.read(["requests_total"], scope=scope)
        assert len(calls) == 2
        await client.read(["requests_total"])
        assert len(calls) == 3
    assert client._http.is_closed
    with pytest.raises(RuntimeError, match="closed"):
        await client.read(["requests_total"])


async def test_failed_scrape_is_shared_and_new_scope_recovers(monkeypatch):
    status = 500
    calls = 0

    def handler(_request):
        nonlocal calls
        calls += 1
        return httpx.Response(status, text="requests_total 7\n")

    with_transport(monkeypatch, handler)
    async with ClientManager() as clients:
        client = await clients.get(PrometheusDataSource("http://metrics"))
        async with DataLoader() as scope:
            for _ in range(2):
                with pytest.raises(PrombedError, match="500"):
                    await client.read(["requests_total"], scope=scope)
        assert calls == 1
        status = 200
        async with DataLoader() as scope:
            result = await client.read(["requests_total"], scope=scope)
        assert calls == 2 and result["requests_total"]["result"]


async def test_oversized_response_and_cancel_release_http_resources(monkeypatch):
    with_transport(monkeypatch, lambda _: httpx.Response(200, content=b"x" * 64))
    async with ClientManager() as clients:
        source = PrometheusDataSource(
            "http://metrics", options=PrometheusOptions(max_scrape_bytes=16)
        )
        client = await clients.get(source)
        with pytest.raises(PrombedError, match="limit"):
            await client.read(["requests_total"])

    started, finished = asyncio.Event(), asyncio.Event()

    async def handler(_request):
        started.set()
        try:
            await asyncio.Event().wait()
        finally:
            finished.set()

    with_transport(monkeypatch, handler)
    async with ClientManager() as clients:
        client = await clients.get(PrometheusDataSource("http://metrics"))
        async with DataLoader() as scope:
            waiter = asyncio.create_task(client.read(["requests_total"], scope=scope))
            await started.wait()
        with pytest.raises(asyncio.CancelledError):
            await waiter
        assert finished.is_set()
    assert client._http.is_closed


async def test_prometheus_pool_and_http_timeout_are_explicit(monkeypatch):
    observed = {}
    factory = httpx.AsyncClient

    def capture(**kwargs):
        observed.update(kwargs)
        return factory(**kwargs)

    monkeypatch.setattr("harness_toolbox.prometheus.httpx.AsyncClient", capture)
    async with ClientManager() as clients:
        await clients.get(
            PrometheusDataSource(
                "http://metrics", options=PrometheusOptions(connection_pool_maxsize=3)
            )
        )
    assert observed["limits"].max_connections == 3
    assert observed["trust_env"] is False


async def test_scrape_has_total_deadline_and_closes_slow_stream(monkeypatch):
    closed = asyncio.Event()

    class SlowStream(httpx.AsyncByteStream):
        async def __aiter__(self):
            yield b"requests_total "
            await asyncio.Event().wait()

        async def aclose(self):
            closed.set()

    with_transport(monkeypatch, lambda _: httpx.Response(200, stream=SlowStream()))
    async with ClientManager() as clients:
        client = await clients.get(
            PrometheusDataSource("http://metrics", options=PrometheusOptions(timeout_ms=20))
        )
        with pytest.raises(PrombedError):
            async with asyncio.timeout(1):
                await client.read(["requests_total"])
        assert closed.is_set()


async def test_fleet_window_uses_weighted_counts_and_preserves_instance_identity(monkeypatch):
    from prombed import ScrapeTarget

    now = 1_000_000
    step = 0
    monkeypatch.setattr("prombed.prombed._now_ms", lambda: now)
    values = {"a": [(100, 10), (110, 11)], "b": [(300, 1), (1200, 4)]}

    def handler(request):
        total, count = values[request.url.host][step]
        return httpx.Response(200, text=f"d_sum {total}\nd_count {count}\n")

    with_transport(monkeypatch, handler)
    source = PrometheusDataSource(
        targets=(ScrapeTarget("http://a/metrics"), ScrapeTarget("http://b/metrics"))
    )
    async with ClientManager() as clients:
        client = await clients.get(source)
        first = await client.read(["d_count"])
        assert len(first["d_count"]["result"]) == 2
        step, now = 1, now + 1000
        await client.read([])
        expression = "sum(increase(d_sum[1001ms])) / sum(increase(d_count[1001ms]))"
        result = client.query_window([expression], start_ms=1_000_000, end_ms=now)
        # (10 + 900)/(1 + 3), not the average of per-replica means (10 + 300)/2.
        assert float(result[expression]["result"][0]["value"][1]) == pytest.approx(227.5)


@pytest.mark.parametrize(
    "options", [PrometheusOptions(retention_ms=500), PrometheusOptions(max_samples_per_series=2)]
)
async def test_window_query_fails_when_history_was_evicted(monkeypatch, options):
    now = 1_000_000
    monkeypatch.setattr("prombed.prombed._now_ms", lambda: now)
    with_transport(monkeypatch, lambda _: httpx.Response(200, text="x 1\n"))
    async with ClientManager() as clients:
        client = await clients.get(PrometheusDataSource("http://metrics", options=options))
        for _ in range(3):
            await client.read([])
            now += 1000
        with pytest.raises(ValueError, match="not retained"):
            client.query_window(["x"], start_ms=1_000_000, end_ms=now - 1000)


async def test_failed_replica_invalidates_window_not_silent_partial_sum(monkeypatch):
    from prombed import ScrapeTarget

    with_transport(
        monkeypatch,
        lambda request: httpx.Response(500 if request.url.host == "bad" else 200, text="x 1\n"),
    )
    async with ClientManager() as clients:
        client = await clients.get(
            PrometheusDataSource(targets=(ScrapeTarget("http://good"), ScrapeTarget("http://bad")))
        )
        with pytest.raises(PrombedError):
            await client.read(["sum(x)"])
        with pytest.raises(ValueError):
            client.query_window(["sum(x)"], start_ms=0, end_ms=1)
