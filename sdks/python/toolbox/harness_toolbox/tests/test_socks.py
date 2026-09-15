import asyncio
import os
from contextlib import asynccontextmanager

import pytest

from harness_toolbox.socks import SocksProxy
from harness_toolbox.transport import Endpoint


async def echo(reader, writer):
    try:
        while data := await reader.read(65536):
            writer.write(data)
            await writer.drain()
    finally:
        writer.close()


async def connect(proxy, address=b"\x01\x0a\x00\x00\x02"):
    reader, writer = await asyncio.open_connection("127.0.0.1", int(proxy.url.rsplit(":", 1)[1]))
    writer.write(b"\x05\x01\x00")
    await writer.drain()
    assert await reader.readexactly(2) == b"\x05\x00"
    writer.write(b"\x05\x01\x00" + address + (8080).to_bytes(2, "big"))
    await writer.drain()
    reply = await reader.readexactly(10)
    return reader, writer, reply


@pytest.mark.parametrize(
    "address,host",
    [
        (b"\x01\x0a\x00\x00\x02", "10.0.0.2"),
        (b"\x03\x03api", "api"),
        (b"\x04" + bytes(15) + b"\x01", "::1"),
    ],
)
async def test_raw_bytes_keepalive_and_scope_cleanup(address, host):
    calls, closed = [], []
    async with await asyncio.start_server(echo, "127.0.0.1", 0) as backend:

        class Transport:
            key = "fixture"

            @asynccontextmanager
            async def connect(self, target):
                calls.append(target)
                try:
                    yield Endpoint("127.0.0.1", backend.sockets[0].getsockname()[1])
                finally:
                    closed.append(target)

        async with SocksProxy(Transport()) as proxy:
            reader, writer, reply = await connect(proxy, address)
            assert reply[1] == 0
            for body in (b"first", b"replacement"):
                payload = (
                    b"POST /upload HTTP/1.1\r\nHost: logical\r\nAuthorization: Bearer fixture\r\n\r\n"
                    + body
                )
                writer.write(payload)
                await writer.drain()
                assert await asyncio.wait_for(reader.readexactly(len(payload)), 2) == payload
        assert calls == closed == [Endpoint(host, 8080)]
        assert await asyncio.wait_for(reader.read(), 2) == b""
        writer.close()
        await writer.wait_closed()


async def test_connection_failure_not_retried_or_logged_with_secrets(caplog):
    calls = []

    class Transport:
        key = "fixture"

        @asynccontextmanager
        async def connect(self, target):
            calls.append(target)
            raise ConnectionError("secret-do-not-log")
            yield

    async with SocksProxy(Transport()) as proxy:
        reader, writer, reply = await connect(proxy)
        assert reply[1] != 0
        writer.close()
        await writer.wait_closed()
    assert len(calls) == 1
    assert "secret-do-not-log" not in caplog.text


async def test_environment_overrides_do_not_mutate_parent(monkeypatch):
    monkeypatch.setenv("HTTP_PROXY", "ambient")
    async with SocksProxy(None) as proxy:
        env = proxy.environment()
        assert env["HTTP_PROXY"] == proxy.url
        assert env["NO_PROXY"] == ""
        assert os.environ["HTTP_PROXY"] == "ambient"
