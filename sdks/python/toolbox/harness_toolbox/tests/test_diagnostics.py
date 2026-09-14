import asyncio
import io
import json
import ssl
import sys
from contextlib import asynccontextmanager, redirect_stdout
from types import SimpleNamespace
from unittest.mock import AsyncMock

import httpx
import pytest
from sqlalchemy.exc import OperationalError

from harness_toolbox import ClientManager, ErrorKind, ToolboxError
from harness_toolbox.errors import (
    MySQLConnectionError,
    MySQLError,
    MySQLQueryError,
    OpenSearchConnectionError,
    OpenSearchError,
    OpenSearchRequestError,
)
from harness_toolbox.mysql import MySQLDataSource, MySQLTarget
from harness_toolbox.opensearch import OpenSearchDataSource, OpenSearchTarget
from harness_toolbox.transport import (
    ConnectionSource,
    DirectTransport,
    Endpoint,
    KubernetesAccess,
    PodPythonTransport,
    PortForwardTransport,
)


@pytest.mark.parametrize(
    "error_type,parent",
    [
        (MySQLConnectionError, MySQLError),
        (MySQLQueryError, MySQLError),
        (OpenSearchConnectionError, OpenSearchError),
        (OpenSearchRequestError, OpenSearchError),
    ],
)
def test_public_contract(error_type, parent):
    error = error_type("safe message", kind=ErrorKind.OPERATION_FAILED, code=42)
    assert isinstance(error, parent)
    assert isinstance(error, ToolboxError)
    assert error.message == str(error) == "safe message"
    assert error.kind == ErrorKind.OPERATION_FAILED
    assert error.code == 42
    assert vars(error) == {
        "message": "safe message",
        "kind": ErrorKind.OPERATION_FAILED,
        "code": 42,
    }


@pytest.mark.parametrize(
    "code,kind",
    [
        (1045, ErrorKind.AUTHENTICATION_FAILED),
        (1049, ErrorKind.RESOURCE_NOT_FOUND),
        (1064, ErrorKind.OPERATION_FAILED),
    ],
)
async def test_mysql_connect_translation_and_no_fallback(monkeypatch, code, kind):
    original = OperationalError("secret SQL", {"password": "secret"}, Exception(code, "secret DSN"))

    @asynccontextmanager
    async def connect():
        raise original
        yield

    engine = SimpleNamespace(connect=connect, dispose=AsyncMock())

    def create(*args, **kwargs):
        return engine

    monkeypatch.setattr("sqlalchemy.ext.asyncio.create_async_engine", create)
    source = MySQLDataSource(
        ConnectionSource(
            "secret-key",
            AsyncMock(return_value=MySQLTarget("db", "secret-user", "secret-password", "records")),
            (DirectTransport(), DirectTransport()),
        )
    )
    client = source.create_client(None)
    try:
        with pytest.raises(MySQLConnectionError) as failure:
            await client.initialize()
        error = failure.value
        assert error.__cause__ is original
        assert (error.kind, error.code) == (kind, code)
        assert "db:3306" in error.message and "records" in error.message
        assert "secret" not in error.message
        assert len(client.diagnostics["attempts"]) == 1
        assert client.diagnostics["selected_transport"] is None
        assert "secret" not in json.dumps(client.diagnostics)
    finally:
        await client.dispose()
    engine.dispose.assert_awaited_once()


@pytest.mark.parametrize("during_connect", [True, False])
async def test_remote_mysql_failure_uses_public_contract(monkeypatch, during_connect):
    class DriverError(Exception):
        pass

    def fail(*args, **kwargs):
        raise DriverError(1049, "private password and SQL")

    def connect(**kwargs):
        if during_connect:
            fail()
        return SimpleNamespace(cursor=fail, close=lambda: None)

    monkeypatch.setitem(
        sys.modules,
        "pymysql",
        SimpleNamespace(
            connect=connect, MySQLError=DriverError, cursors=SimpleNamespace(SSCursor=object)
        ),
    )

    async def execute(access, pod, command, *, stdin, **kwargs):
        output = io.StringIO()
        with monkeypatch.context() as patch, redirect_stdout(output):
            patch.setattr(sys, "stdin", io.StringIO(stdin.decode()))
            exec(command[-1], {})
        assert "private" not in output.getvalue()
        return output.getvalue().encode()

    monkeypatch.setattr(KubernetesAccess, "execute", execute)
    source = MySQLDataSource(
        ConnectionSource(
            "db",
            AsyncMock(return_value=MySQLTarget("db", "user", "secret")),
            (PodPythonTransport(KubernetesAccess(None, "ns"), "pod"),),
        )
    )
    async with ClientManager() as clients:
        with pytest.raises(MySQLConnectionError if during_connect else MySQLQueryError) as failure:
            client = await clients.get(source)
            assert client.diagnostics["selected_transport"] == "pod-python"
            await client.query("private SQL")
    error = failure.value
    assert (error.kind, error.code) == (ErrorKind.RESOURCE_NOT_FOUND, 1049)
    assert "private" not in str(error)
    # Remote driver objects cannot cross the process boundary; no fabricated cause.
    assert error.__cause__ is None


async def test_tls_translation_does_not_modify_native_exception(monkeypatch):
    original = httpx.ConnectError("private URL")
    original.__cause__ = ssl.SSLCertVerificationError(1, "private certificate path")
    monkeypatch.setattr(httpx.AsyncClient, "head", AsyncMock(side_effect=original))
    source = OpenSearchDataSource(
        ConnectionSource(
            "private-key",
            AsyncMock(
                return_value=OpenSearchTarget(
                    "https://search:9200/private?password=private",
                    "private-user",
                    "private-password",
                    servername="search.internal",
                )
            ),
        )
    )
    async with ClientManager() as clients:
        with pytest.raises(OpenSearchConnectionError) as failure:
            await clients.get(source)
    assert failure.value.kind == ErrorKind.TLS_VERIFICATION_FAILED
    assert failure.value.__cause__ is original
    assert "search:9200" in failure.value.message
    assert "private" not in str(failure.value)
    assert "_toolbox_access" not in vars(original)


@pytest.mark.parametrize(
    "status,kind",
    [
        (401, ErrorKind.AUTHENTICATION_FAILED),
        (403, ErrorKind.PERMISSION_DENIED),
        (404, ErrorKind.RESOURCE_NOT_FOUND),
        (500, ErrorKind.OPERATION_FAILED),
    ],
)
async def test_http_probe_failure_has_protocol_code(monkeypatch, status, kind):
    response = httpx.Response(status, request=httpx.Request("HEAD", "http://search/private"))
    monkeypatch.setattr(httpx.AsyncClient, "head", AsyncMock(return_value=response))
    source = OpenSearchDataSource(
        ConnectionSource("search", AsyncMock(return_value=OpenSearchTarget("http://search")))
    )
    async with ClientManager() as clients:
        with pytest.raises(OpenSearchConnectionError) as failure:
            await clients.get(source)
    assert (failure.value.kind, failure.value.code) == (kind, status)
    assert isinstance(failure.value.__cause__, httpx.HTTPStatusError)
    assert "private" not in str(failure.value)


@pytest.mark.parametrize(
    "native",
    [
        TypeError("programming error"),
        ValueError("invalid argument"),
        asyncio.CancelledError(),
        MySQLConnectionError("already translated", kind=ErrorKind.OPERATION_FAILED),
    ],
)
async def test_mysql_connection_does_not_wrap_programming_errors_or_cancellation(
    monkeypatch, native
):
    @asynccontextmanager
    async def connect():
        raise native
        yield

    engine = SimpleNamespace(connect=connect, dispose=AsyncMock())
    monkeypatch.setattr(
        "sqlalchemy.ext.asyncio.create_async_engine", lambda *args, **kwargs: engine
    )
    source = MySQLDataSource(
        ConnectionSource("db", AsyncMock(return_value=MySQLTarget("db", "user", "password")))
    )
    client = source.create_client(None)
    try:
        with pytest.raises(type(native)) as failure:
            await client.initialize()
    finally:
        await client.dispose()
    assert failure.value is native
    engine.dispose.assert_awaited_once()


async def test_resolution_belongs_to_caller():
    original = RuntimeError("configuration unavailable")
    source = MySQLDataSource(ConnectionSource("db", AsyncMock(side_effect=original)))
    async with ClientManager() as clients:
        with pytest.raises(RuntimeError) as failure:
            await clients.get(source)
    assert failure.value is original


async def test_http_fallback_records_selected_port_forward(monkeypatch):
    @asynccontextmanager
    async def tunnel(self, target):
        yield Endpoint("127.0.0.1", 12345, target.host)

    monkeypatch.setattr(PortForwardTransport, "connect", tunnel)
    probe = AsyncMock(
        side_effect=[
            httpx.ConnectError("unreachable"),
            httpx.Response(200, request=httpx.Request("HEAD", "http://search/")),
        ]
    )
    monkeypatch.setattr(httpx.AsyncClient, "head", probe)
    source = OpenSearchDataSource(
        ConnectionSource(
            "search",
            AsyncMock(return_value=OpenSearchTarget("http://search:9200")),
            (
                DirectTransport(),
                PortForwardTransport(KubernetesAccess(None, "ns"), "pod/search", 9200),
            ),
        )
    )
    async with ClientManager() as clients:
        client = await clients.get(source)
        snapshot = client.diagnostics
        assert snapshot["selected_transport"] == "port-forward"
        assert [(a["transport"], a["status"]) for a in snapshot["attempts"]] == [
            ("direct", "error"),
            ("port-forward", "ok"),
        ]
        snapshot["attempts"].clear()
        assert len(client.diagnostics["attempts"]) == 2
        assert probe.await_count == 2
