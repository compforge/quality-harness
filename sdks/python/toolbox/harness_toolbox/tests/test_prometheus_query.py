import asyncio
from dataclasses import replace
from urllib.parse import parse_qs

import httpx
import pytest
from harness_common.client import ClientManager

from harness_toolbox.data_loader import DataLoader
from harness_toolbox.errors import ErrorKind, PrometheusQueryError
from harness_toolbox.http import HTTPClient, HTTPClientProvider
from harness_toolbox.prometheus_query import PrometheusQueryDataSource, PrometheusQueryOptions


def success(value="3", **annotations):
    return {
        "status": "success",
        "data": {"resultType": "scalar", "result": [123, value]},
        **annotations,
    }


def with_transport(monkeypatch, handler):
    pools = []

    def create(provider, clients):
        pool = HTTPClient(
            transport=httpx.MockTransport(handler),
            limits=httpx.Limits(max_connections=provider.max_connections),
            timeout=provider.timeout_s,
            trust_env=False,
        )
        pools.append((provider, pool))
        return pool

    monkeypatch.setattr(HTTPClientProvider, "create_client", create)
    return pools


async def test_scoped_queries_share_timestamp_requests_and_owned_pool(monkeypatch):
    requests = []

    def handler(request):
        requests.append(request)
        return httpx.Response(200, json=success(infos=["annotation"]))

    pools = with_transport(monkeypatch, handler)
    source = PrometheusQueryDataSource(
        "https://prom.example/proxy/",
        headers={"Authorization": "secret"},
        options=PrometheusQueryOptions(connection_pool_maxsize=3),
    )
    assert "secret" not in repr(source)
    async with ClientManager() as clients:
        client = await clients.get(source)
        assert client is await clients.get(replace(source))
        async with DataLoader() as scope:
            a, b = await asyncio.gather(
                client.read(["up", "sum(up)"], scope=scope),
                client.read(["sum(up)"], scope=scope),
            )
            assert a["sum(up)"] is b["sum(up)"]
            assert b["sum(up)"].infos == ("annotation",)
            assert len(requests) == 2
            # Explicit timestamps are distinct reads, even within the same scope.
            await client.read(["sum(up)"], timestamp=42, scope=scope)
        forms = [parse_qs(r.content.decode()) for r in requests]
        assert forms[0]["time"] == forms[1]["time"]
        assert forms[2]["time"] == ["42"]
        assert all(r.method == "POST" and r.url.path == "/proxy/api/v1/query" for r in requests)
        assert all(not r.url.query and r.headers["Authorization"] == "secret" for r in requests)
        assert all("limit" not in form for form in forms)
        async with DataLoader() as scope:
            await client.read(["up"], scope=scope)
        assert len(requests) == 4
        assert client is not await clients.get(
            replace(source, headers={"Authorization": "different"})
        )
        assert len(pools) == 2 and pools[0][0].max_connections == 3
    assert all(pool.is_closed for _, pool in pools)
    with pytest.raises(RuntimeError, match="closed"):
        await client.read(["up"])


@pytest.mark.parametrize(
    "response,kind",
    [
        (httpx.Response(401, text="secret-query"), ErrorKind.AUTHENTICATION_FAILED),
        (httpx.Response(403, text="secret-query"), ErrorKind.PERMISSION_DENIED),
        (httpx.Response(503, text="secret-query"), ErrorKind.OPERATION_FAILED),
        (
            httpx.Response(200, json={"status": "error", "error": "secret-query"}),
            ErrorKind.OPERATION_FAILED,
        ),
        (httpx.Response(200, content=b"secret-query"), ErrorKind.INVALID_RESPONSE),
        (httpx.Response(200, json={"status": "success", "data": []}), ErrorKind.INVALID_RESPONSE),
    ],
)
async def test_errors_are_typed_safe_and_shared_within_scope(monkeypatch, response, kind):
    calls = []

    def handler(request):
        calls.append(request)
        return response

    with_transport(monkeypatch, handler)
    async with ClientManager() as clients:
        client = await clients.get(PrometheusQueryDataSource("http://prom"))
        async with DataLoader() as scope:
            for _ in range(2):
                with pytest.raises(PrometheusQueryError) as error:
                    await client.read(["secret-query"], scope=scope)
                assert error.value.kind == kind
                assert "secret-query" not in str(error.value)
        assert len(calls) == 1


@pytest.mark.parametrize(
    "options,payload",
    [
        (PrometheusQueryOptions(max_response_bytes=5), success()),
        (
            PrometheusQueryOptions(max_series=1),
            {
                "status": "success",
                "data": {
                    "resultType": "vector",
                    "result": [
                        {"metric": {"instance": str(i)}, "value": [1, "2"]} for i in range(2)
                    ],
                },
            },
        ),
    ],
)
async def test_response_limits_fail_instead_of_truncating(monkeypatch, options, payload):
    with_transport(monkeypatch, lambda _: httpx.Response(200, json=payload))
    async with ClientManager() as clients:
        client = await clients.get(PrometheusQueryDataSource("http://prom", options=options))
        with pytest.raises(PrometheusQueryError) as error:
            await client.read(["up"])
        assert error.value.kind == ErrorKind.LIMIT_EXCEEDED


async def test_empty_vectors_and_annotations_are_preserved(monkeypatch):
    payload = {
        "status": "success",
        "data": {"resultType": "vector", "result": []},
        "warnings": ["partial response"],
        "infos": ["advisory"],
    }
    with_transport(monkeypatch, lambda _: httpx.Response(200, json=payload))
    async with ClientManager() as clients:
        client = await clients.get(PrometheusQueryDataSource("http://prom"))
        result = (await client.read(["up"]))["up"]
        assert result.data["result"] == []
        assert result.warnings == ("partial response",) and result.infos == ("advisory",)


@pytest.mark.parametrize("cancel", [False, True])
async def test_deadline_and_scope_cancellation_close_streams(monkeypatch, cancel):
    started, closed = asyncio.Event(), asyncio.Event()

    class SlowStream(httpx.AsyncByteStream):
        async def __aiter__(self):
            started.set()
            yield b'{"status":'
            await asyncio.Event().wait()

        async def aclose(self):
            closed.set()

    pools = with_transport(monkeypatch, lambda _: httpx.Response(200, stream=SlowStream()))
    async with ClientManager() as clients:
        client = await clients.get(
            PrometheusQueryDataSource(
                "http://prom", options=PrometheusQueryOptions(timeout_ms=5000 if cancel else 20)
            )
        )
        if cancel:
            async with DataLoader() as scope:
                waiter = asyncio.create_task(client.read(["up"], scope=scope))
                await started.wait()
            with pytest.raises(asyncio.CancelledError):
                await waiter
        else:
            with pytest.raises(PrometheusQueryError) as error:
                await client.read(["up"])
            assert error.value.kind == ErrorKind.TIMEOUT
        assert closed.is_set()
    assert pools[0][1].is_closed


def test_query_source_is_offline_and_validates_endpoint_and_limits():
    for url in ("", "ftp://prom", "http://user:secret@prom", "http://prom?query=x"):
        with pytest.raises(ValueError):
            PrometheusQueryDataSource(url)
    with pytest.raises(ValueError):
        PrometheusQueryDataSource("http://prom", options=PrometheusQueryOptions(timeout_ms=0))
