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

from harness_toolbox import ClientManager
from harness_toolbox.diagnostics import error_details
from harness_toolbox.mysql import MySQLDataSource, MySQLTarget, RemoteMySQLError
from harness_toolbox.opensearch import OpenSearchDataSource, OpenSearchTarget
from harness_toolbox.transport import (
    ConnectionSource,
    DirectTransport,
    KubernetesAccess,
    PodPythonTransport,
)


def test_cause_groups_cycles_and_redaction():
    original = ssl.SSLCertVerificationError(1, "secret credentials")
    wrapper = RuntimeError("private query")
    wrapper.__cause__ = original
    original.__cause__ = wrapper
    result = error_details(ExceptionGroup("private DSN", [wrapper, original]))
    assert result["kind"] == "tls_verification_failed"
    assert len(result["causes"]) == 3
    assert "secret" not in json.dumps(result)
    assert "private" not in json.dumps(result)
    many = error_details(ExceptionGroup("many", [ValueError(str(i)) for i in range(30)]))
    assert len(many["causes"]) == 16
    assert many["causes_truncated"] is True


@pytest.mark.parametrize(
    "code,kind",
    [(1045, "authentication_failed"), (1049, "database_not_found"), (1064, "query_error")],
)
async def test_mysql_failed_connect_keeps_native_error_and_no_fallback(monkeypatch, code, kind):
    error = OperationalError("secret SQL", {"password": "secret"}, Exception(code, "secret DSN"))

    @asynccontextmanager
    async def connect():
        raise error
        yield

    engine = SimpleNamespace(connect=connect, dispose=AsyncMock())

    def create(*args, **kwargs):
        return engine

    monkeypatch.setattr("sqlalchemy.ext.asyncio.create_async_engine", create)

    async def resolve():
        return MySQLTarget("db", "secret-user", "secret-password", "records")

    source = MySQLDataSource(
        ConnectionSource("secret-key", resolve, (DirectTransport(), DirectTransport()))
    )
    async with ClientManager() as clients:
        with pytest.raises(OperationalError) as failure:
            await clients.get(source)
    assert failure.value is error
    result = error_details(error)
    assert result["kind"] == kind
    assert result["access"]["stage"] == "connect"
    assert result["access"]["target"] == {"host": "db", "port": 3306, "database": "records"}
    assert len(result["access"]["attempts"]) == 1
    assert result["access"]["selected_transport"] is None
    assert "secret" not in json.dumps(result)
    engine.dispose.assert_awaited_once()


@pytest.mark.parametrize("during_connect", [True, False])
async def test_remote_mysql_code_survives_pod_transport(monkeypatch, during_connect):
    class MySQLError(Exception):
        pass

    def fail(*args, **kwargs):
        raise MySQLError(1049, "private database, password and SQL")

    def connect(**kwargs):
        if during_connect:
            fail()
        return SimpleNamespace(cursor=fail, close=lambda: None)

    monkeypatch.setitem(
        sys.modules,
        "pymysql",
        SimpleNamespace(
            connect=connect, MySQLError=MySQLError, cursors=SimpleNamespace(SSCursor=object)
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
        with pytest.raises(RemoteMySQLError) as failure:
            client = await clients.get(source)
            assert client.diagnostics["selected_transport"] == "pod-python"
            await client.query("private SQL")
    result = error_details(failure.value)
    assert result["kind"] == "database_not_found"
    assert result["access"]["stage"] == ("connect" if during_connect else "query")
    assert "private" not in json.dumps(result)


async def test_tls_cause_and_detached_connection_snapshot(monkeypatch):
    failure = httpx.ConnectError("CERTIFICATE_VERIFY_FAILED private-url")
    monkeypatch.setattr(httpx.AsyncClient, "head", AsyncMock(side_effect=failure))
    source = OpenSearchDataSource(
        ConnectionSource(
            "private-key",
            AsyncMock(
                return_value=OpenSearchTarget(
                    "https://search:9200/private/path?password=private",
                    "private-user",
                    "private-password",
                    servername="search.internal",
                )
            ),
        )
    )
    async with ClientManager() as clients:
        with pytest.raises(httpx.ConnectError):
            await clients.get(source)
    result = error_details(failure)
    assert result["kind"] == "tls_verification_failed"
    assert result["access"]["target"] == {
        "host": "search",
        "port": 9200,
        "servername": "search.internal",
    }
    result["access"]["target"]["host"] = "changed"
    assert error_details(failure)["access"]["target"]["host"] == "search"
    assert "private" not in json.dumps(error_details(failure))


async def test_resolution_failure_and_cancellation(monkeypatch):
    failure = RuntimeError("private configuration")
    for error in (failure, asyncio.CancelledError()):
        source = MySQLDataSource(ConnectionSource("key", AsyncMock(side_effect=error)))
        client = source.create_client(None)
        with pytest.raises(type(error)):
            await client.initialize()
        assert client.diagnostics["attempts"] == []
        assert client.diagnostics["target"] is None
        if isinstance(error, asyncio.CancelledError):
            assert "access" not in error_details(error)
        else:
            assert error_details(error)["access"]["stage"] == "resolve"


async def test_http_fallback_records_selected_port_forward(monkeypatch):
    from harness_toolbox.transport import Endpoint, PortForwardTransport

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
        assert client.diagnostics["selected_transport"] == "port-forward"
        assert [(item["transport"], item["status"]) for item in client.diagnostics["attempts"]] == [
            ("direct", "error"),
            ("port-forward", "ok"),
        ]
        assert client.diagnostics["target"]["host"] == "search"
        assert probe.await_count == 2
