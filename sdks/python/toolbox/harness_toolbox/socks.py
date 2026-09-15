"""Loopback SOCKS5 adapter for clients that cannot consume a Python Transport."""

from __future__ import annotations

import asyncio
import ipaddress
import logging
from contextlib import AsyncExitStack

from harness_toolbox.transport import Endpoint, Transport

LOG = logging.getLogger(__name__)


async def _destination(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> Endpoint:
    version, count = await reader.readexactly(2)
    methods = await reader.readexactly(count)
    if version != 5 or 0 not in methods:
        writer.write(b"\x05\xff")
        await writer.drain()
        raise ValueError("SOCKS5 no-auth is required")
    writer.write(b"\x05\x00")
    await writer.drain()
    version, command, reserved, kind = await reader.readexactly(4)
    if (version, command, reserved) != (5, 1, 0):
        raise ValueError("only SOCKS5 CONNECT is supported")
    if kind == 3:
        length = (await reader.readexactly(1))[0]
        host = (await reader.readexactly(length)).decode("ascii")
    elif kind in (1, 4):
        host = str(ipaddress.ip_address(await reader.readexactly(4 if kind == 1 else 16)))
    else:
        raise ValueError("unsupported SOCKS5 address type")
    return Endpoint(host, int.from_bytes(await reader.readexactly(2), "big"))


async def _copy(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
    while data := await reader.read(65536):
        writer.write(data)
        await writer.drain()
    if writer.can_write_eof():
        writer.write_eof()


class SocksProxy:
    """Own a bounded loopback-only CONNECT proxy; transport owns destination policy.

    HTTP headers, TLS SNI, payloads and keep-alive bytes are not interpreted or
    replayed. The caller must close clients before leaving this proxy's scope.
    Connection metadata may be logged, never payloads or credentials.
    """

    def __init__(
        self, transport: Transport, *, setup_timeout_s: float = 30, max_connections: int = 128
    ):
        if max_connections < 1 or setup_timeout_s <= 0:
            raise ValueError("proxy limits must be positive")
        self.transport = transport
        self.setup_timeout_s = setup_timeout_s
        self.max_connections = max_connections
        self._connections: set[asyncio.Task] = set()
        self._server: asyncio.Server | None = None

    async def __aenter__(self) -> SocksProxy:
        self._server = await asyncio.start_server(self._handle, "127.0.0.1", 0)
        return self

    @property
    def url(self) -> str:
        if self._server is None:
            raise RuntimeError("proxy scope is not active")
        return f"socks5://127.0.0.1:{self._server.sockets[0].getsockname()[1]}"

    def environment(self) -> dict[str, str]:
        """Return child-process overrides; never modify the parent environment."""
        return {
            **dict.fromkeys(
                (
                    "HTTP_PROXY",
                    "HTTPS_PROXY",
                    "http_proxy",
                    "https_proxy",
                    "ALL_PROXY",
                    "all_proxy",
                ),
                self.url,
            ),
            "NO_PROXY": "",
            "no_proxy": "",
        }

    async def _handle(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        task = asyncio.current_task()
        if len(self._connections) >= self.max_connections:
            writer.close()
            return
        self._connections.add(task)
        upstream = None
        established = False
        try:
            async with AsyncExitStack() as stack:
                async with asyncio.timeout(self.setup_timeout_s):
                    target = await _destination(reader, writer)
                    endpoint = await stack.enter_async_context(self.transport.connect(target))
                    remote, upstream = await asyncio.open_connection(endpoint.host, endpoint.port)
                    writer.write(b"\x05\x00\x00\x01\x7f\x00\x00\x01\x00\x00")
                    await writer.drain()
                    established = True
                async with asyncio.TaskGroup() as group:
                    group.create_task(_copy(reader, upstream))
                    group.create_task(_copy(remote, writer))
        except Exception as exc:
            LOG.warning("SOCKS connection failed (%s); no fallback or replay", type(exc).__name__)
            if not established:
                writer.write(b"\x05\x01\x00\x01\x00\x00\x00\x00\x00\x00")
        finally:
            writer.close()
            if upstream:
                upstream.close()
            self._connections.discard(task)

    async def __aexit__(self, *exc) -> None:
        if self._server:
            self._server.close()
        pending = list(self._connections)
        for task in pending:
            task.cancel()
        await asyncio.gather(*pending, return_exceptions=True)
        # Python 3.13 waits for accepted connections too. Retire those before
        # waiting for the listener, otherwise active keep-alives deadlock exit.
        if self._server:
            await self._server.wait_closed()
            self._server = None
