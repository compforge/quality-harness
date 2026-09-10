import asyncio
import json
from contextlib import aclosing, asynccontextmanager, suppress

import httpx
import pytest

from harness_toolbox import ClientManager
from harness_toolbox.opensearch import OpenSearchDataSource, OpenSearchTarget
from harness_toolbox.transport import ConnectionSource


@asynccontextmanager
async def server(handler):
    tasks = set()

    async def handle(reader, writer):
        task = asyncio.current_task()
        tasks.add(task)
        try:
            while True:
                try:
                    head = await reader.readuntil(b"\r\n\r\n")
                except (asyncio.IncompleteReadError, ConnectionResetError):
                    break
                method, path, _ = head.split(b"\r\n")[0].decode().split()
                length = next(
                    (
                        int(line.split(b":")[1])
                        for line in head.lower().split(b"\r\n")
                        if line.startswith(b"content-length:")
                    ),
                    0,
                )
                body = await reader.readexactly(length)
                status, result = handler(method, path, json.loads(body) if body else None)
                data = json.dumps(result).encode()
                writer.write(
                    f"HTTP/1.1 {status} Response\r\nContent-Length: {len(data)}\r\nContent-Type: application/json\r\n\r\n".encode()
                )
                if method != "HEAD":
                    writer.write(data)
                await writer.drain()
        finally:
            writer.close()
            with suppress(ConnectionResetError):
                await writer.wait_closed()
            tasks.discard(task)

    listener = await asyncio.start_server(handle, "127.0.0.1", 0)
    try:
        yield f"http://127.0.0.1:{listener.sockets[0].getsockname()[1]}"
    finally:
        listener.close()
        await listener.wait_closed()
        await asyncio.gather(*tasks)


def source(url, **kwargs):
    async def resolve():
        return OpenSearchTarget(url)

    return OpenSearchDataSource(ConnectionSource(url, resolve), **kwargs)


async def test_scroll_cleanup_on_early_stop_and_single_initialization():
    calls = []

    def handle(method, path, body):
        calls.append((method, path, body))
        return 200, {"_scroll_id": "scroll-1", "hits": {"hits": [{"_source": {"id": 1}}]}}

    async with server(handle) as url, ClientManager() as clients:
        first, second = await asyncio.gather(clients.get(source(url)), clients.get(source(url)))
        assert first is second
        async with aclosing(first.scroll("spans", {"match_all": {}})) as pages:
            async for page in pages:
                assert page[0]["_source"]["id"] == 1
                break
    assert [call[0] for call in calls] == ["HEAD", "POST", "DELETE"]
    assert calls[-1][2] == {"scroll_id": ["scroll-1"]}


async def test_response_limit_and_protocol_error_do_not_retry():
    count = 0

    def handle(method, path, body):
        nonlocal count
        if method == "HEAD":
            return 200, {}
        count += 1
        return (403, {}) if path == "/denied" else (200, {"large": "x" * 100})

    async with server(handle) as url, ClientManager() as clients:
        client = await clients.get(source(url, max_response_bytes=20))
        with pytest.raises(ValueError, match="byte limit"):
            await client.request("GET", "/large")
        with pytest.raises(httpx.HTTPStatusError):
            await client.request("GET", "/denied")
        with pytest.raises(ValueError, match="relative"):
            await client.request("GET", "https://elsewhere.example/")
    assert count == 2
