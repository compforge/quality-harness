import asyncio
from dataclasses import replace
from itertools import count

import httpx
import pytest
from harness_common.access.client import ClientManager
from prombed import PrombedError

from harness_toolbox.data_loader import DataLoader
from harness_toolbox.metric import MetricClient, MetricDataSource, MetricOptions
from harness_toolbox.prometheus import PrometheusDataSource


@pytest.fixture(autouse=True)
def scrape_clock(monkeypatch):
    # Separate observation cycles even when MockTransport completes in one millisecond.
    clock = count(1_000_000, 10)
    monkeypatch.setattr("prombed.prombed._now_ms", lambda: next(clock))


def with_transport(monkeypatch, handler):
    monkeypatch.setattr(
        MetricDataSource,
        "create_client",
        lambda source, _: MetricClient(source, transport=httpx.MockTransport(handler)),
    )


async def test_datasource_shares_client_and_scoped_scrape_but_preserves_raw_labels(monkeypatch):
    calls = []

    def handler(request):
        calls.append(request)
        return httpx.Response(200, text='requests_total{path="/chat"} 7\n')

    with_transport(monkeypatch, handler)
    source = MetricDataSource("http://metrics/metrics", headers={"Authorization": "secret"})
    assert "secret" not in repr(source)
    async with ClientManager() as clients:
        client = await clients.get(source)
        assert client is await clients.get(replace(source))
        assert client is not await clients.get(replace(source, headers={"Authorization": "other"}))
        assert client is not await clients.get(
            replace(source, options=MetricOptions(max_series=10))
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
        client = await clients.get(MetricDataSource("http://metrics"))
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
        source = MetricDataSource("http://metrics", options=MetricOptions(max_scrape_bytes=16))
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
        client = await clients.get(MetricDataSource("http://metrics"))
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

    monkeypatch.setattr("harness_toolbox.metric.httpx.AsyncClient", capture)
    async with ClientManager() as clients:
        await clients.get(
            MetricDataSource("http://metrics", options=MetricOptions(connection_pool_maxsize=3))
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
            MetricDataSource("http://metrics", options=MetricOptions(timeout_ms=20))
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
    source = MetricDataSource(
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
    "options", [MetricOptions(retention_ms=500), MetricOptions(max_samples_per_series=2)]
)
async def test_window_query_fails_when_history_was_evicted(monkeypatch, options):
    now = 1_000_000
    monkeypatch.setattr("prombed.prombed._now_ms", lambda: now)
    with_transport(monkeypatch, lambda _: httpx.Response(200, text="x 1\n"))
    async with ClientManager() as clients:
        client = await clients.get(MetricDataSource("http://metrics", options=options))
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
            MetricDataSource(targets=(ScrapeTarget("http://good"), ScrapeTarget("http://bad")))
        )
        with pytest.raises(PrombedError):
            await client.read(["sum(x)"])
        with pytest.raises(ValueError):
            client.query_window(["sum(x)"], start_ms=0, end_ms=1)


async def test_metric_family_filter_bounds_storage_and_keeps_histogram_children(monkeypatch):
    noise = "".join(f'noise{{id="{n}"}} {n}\n' for n in range(100))
    text = noise + 'api_total 7\nlatency_sum 4\nlatency_count 2\nlatency_bucket{le="+Inf"} 2\n'
    with_transport(monkeypatch, lambda request: httpx.Response(200, text=text))
    source = MetricDataSource(
        "http://metrics",
        options=MetricOptions(max_series=20),
        metric_names=("api_total", "latency"),
    )
    async with ClientManager() as clients:
        client = await clients.get(source)
        result = await client.read(["sum(api_total)", "sum(latency_count)", "noise"])
        assert float(result["sum(api_total)"]["result"][0]["value"][1]) == 7
        assert float(result["sum(latency_count)"]["result"][0]["value"][1]) == 2
        assert result["noise"]["result"] == []
        assert client is not await clients.get(replace(source, metric_names=("api_total",)))
        # Filtering happens after bounded download; it cannot bypass the byte budget.
        limited = await clients.get(replace(source, options=MetricOptions(max_scrape_bytes=8)))
        with pytest.raises(PrombedError):
            await limited.read(["api_total"])


async def test_partial_fleet_preserves_samples_and_target_failure_stage(monkeypatch):
    from prombed import ScrapeTarget

    class Slow(httpx.AsyncByteStream):
        async def __aiter__(self):
            yield b"x "
            await asyncio.Event().wait()

        async def aclose(self):
            pass

    def handle(request):
        return (
            httpx.Response(200, stream=Slow())
            if request.url.host == "slow"
            else httpx.Response(200, text="x 7\n")
        )

    with_transport(monkeypatch, handle)
    source = MetricDataSource(
        targets=(ScrapeTarget("http://good"), ScrapeTarget("http://slow")),
        options=MetricOptions(timeout_ms=30),
    )
    async with ClientManager() as owner:
        client = await owner.get(source)
        with pytest.raises(PrombedError, match="timeout"):
            await client.read(["sum(x)"])
        rows = client.diagnostics
        assert rows["http://good"]["status"] == "ok"
        assert rows["http://slow"]["stage"] == "body"
        assert rows["http://slow"]["decoded_bytes"] == 2
        assert client._runtime.query("x", time_ms=1_000_100)["data"]["result"]
        with pytest.raises(ValueError):
            client.query_window(["sum(x)"], start_ms=0, end_ms=1)
        rows.clear()
        assert client.diagnostics


@pytest.mark.parametrize("kind", ["prometheus", "victoriametrics", "custom_backend"])
def test_backend_kind_is_explicit_and_part_of_client_identity(kind):
    source = PrometheusDataSource("http://metrics", kind=kind)
    assert source.kind == kind
    assert source.client_key == PrometheusDataSource("http://metrics", kind=kind).client_key
    if kind != "prometheus":
        assert source.client_key != PrometheusDataSource("http://metrics").client_key


@pytest.mark.parametrize("kind", [None, "", " ", 1])
def test_invalid_backend_kind_is_rejected(kind):
    with pytest.raises(ValueError, match="kind"):
        PrometheusDataSource("http://metrics", kind=kind)
