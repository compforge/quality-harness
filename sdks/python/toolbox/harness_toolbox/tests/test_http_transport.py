import asyncio
from contextlib import asynccontextmanager

import httpx
import pytest
from harness_common import ClientManager

from harness_toolbox.http import HTTPClientProvider
from harness_toolbox.transport import Endpoint


async def test_routed_http_preserves_host_reuses_connection_and_closes_in_order():
    events, requests = [], []

    async def serve(reader, writer):
        try:
            while True:
                data = await reader.readuntil(b"\r\n\r\n")
                requests.append(data)
                writer.write(b"HTTP/1.1 200 OK\r\nContent-Length: 2\r\n\r\nok")
                await writer.drain()
        except asyncio.IncompleteReadError:
            pass
        finally:
            writer.close()
            await writer.wait_closed()
            events.append("socket-closed")

    server = await asyncio.start_server(serve, "127.0.0.1", 0)
    port = server.sockets[0].getsockname()[1]

    class Route:
        client_key = "test-route"
        key = "test-route"

        def create_client(self, clients):
            return self

        async def initialize(self):
            events.append("route-open")

        async def dispose(self):
            events.append("route-close")

        @asynccontextmanager
        async def connect(self, target):
            assert target.host == "unresolvable.invalid" and target.port == 8001
            events.append("connect")
            try:
                yield Endpoint("127.0.0.1", port, target.host)
            finally:
                events.append("connection-released")

    try:
        async with ClientManager() as owner:
            source = HTTPClientProvider(2, 2, transport=Route())
            client = await owner.get(source)
            assert client is await owner.get(source)
            for _ in range(2):
                response = await client.get("http://unresolvable.invalid:8001/metrics")
                assert response.text == "ok"
            assert not client.is_closed
        assert client.is_closed
        assert len(requests) == 2
        assert all(b"Host: unresolvable.invalid:8001" in request for request in requests)
        assert events.count("connect") == 1
        assert events.index("connection-released") < events.index("route-close")
    finally:
        server.close()
        await server.wait_closed()


async def test_route_failure_does_not_fall_back_or_replay():
    calls = []

    class Route:
        key = client_key = "broken-route"

        def create_client(self, clients):
            return self

        async def initialize(self):
            pass

        async def dispose(self):
            pass

        @asynccontextmanager
        async def connect(self, target):
            calls.append(target)
            raise ConnectionError("route unavailable")
            yield

    async with ClientManager() as owner:
        client = await owner.get(HTTPClientProvider(1, 1, transport=Route()))
        with pytest.raises(httpx.ProxyError):
            await client.get("http://127.0.0.1:9/metrics")
    assert len(calls) == 1
