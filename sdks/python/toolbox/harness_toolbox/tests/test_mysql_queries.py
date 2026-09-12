"""Exercise SQLAlchemy's real asyncmy adapter and the embedded Pod script without a DB."""

import asyncio
import io
import sys
from contextlib import redirect_stdout
from datetime import date, datetime, time, timedelta
from decimal import Decimal
from types import SimpleNamespace

import asyncmy
import pytest
from asyncmy.cursors import Cursor
from sqlalchemy.dialects.mysql.asyncmy import MySQLDialect_asyncmy

from harness_toolbox import ClientManager
from harness_toolbox.mysql import MySQLDataSource, MySQLTarget
from harness_toolbox.transport import ConnectionSource, KubernetesAccess, PodPythonTransport


class Database:
    def __init__(self):
        self.rows = [(1,)]
        self.columns = ("value",)
        self.params = None
        self.statements = []
        self.closed = 0
        self.delay = 0
        self.active = 0
        self.max_active = 0
        # Only the real driver's escaping is used; this connection never opens a socket.
        connection = asyncmy.Connection()
        connection.server_status = 0
        self.formatter = Cursor(connection)

    def execute(self, sql, params):
        self.statements.append(self.formatter.mogrify(sql, params))
        self.params = params

    async def pause(self):
        self.active += 1
        self.max_active = max(self.max_active, self.active)
        try:
            await asyncio.sleep(self.delay)
        finally:
            self.active -= 1


class AsyncCursor:
    arraysize = 1
    lastrowid = 0

    def __init__(self, database):
        self.database = database
        self.rows = []
        self.description = None
        self.rowcount = -1

    async def __aenter__(self):
        return self

    async def execute(self, sql, params=None):
        self.database.execute(sql, params)
        await self.database.pause()
        self.description = (
            tuple((name, 253, None, None, None, None, None) for name in self.database.columns)
            or None
        )
        self.rows = list(self.database.rows)
        self.rowcount = 18446744073709551615 if self.description else 3

    async def fetchone(self):
        return self.rows.pop(0) if self.rows else None

    async def fetchmany(self, size):
        rows, self.rows = self.rows[:size], self.rows[size:]
        return rows

    async def fetchall(self):
        rows, self.rows = self.rows, []
        return rows

    async def close(self):
        self.database.closed += 1


class AsyncConnection:
    def __init__(self, database):
        self.database = database

    def cursor(self, *args):
        return AsyncCursor(self.database)

    async def rollback(self):
        pass

    async def autocommit(self, value):
        pass

    def get_autocommit(self):
        return True

    async def ensure_closed(self):
        pass

    def close(self):
        pass


class PodCursor:
    def __init__(self, database):
        self.database = database
        self.description = None
        self.rowcount = -1

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.database.closed += 1

    def execute(self, sql, params):
        self.database.execute(sql, params)
        self.description = [(name,) for name in self.database.columns] or None
        self.rowcount = 18446744073709551615 if self.description else 3

    def fetchmany(self, size):
        return self.database.rows[:size]


@pytest.fixture(params=["direct", "pod"])
async def query_client(request, monkeypatch):
    database = Database()
    if request.param == "direct":

        async def connect(*args, **kwargs):
            return AsyncConnection(database)

        monkeypatch.setattr(asyncmy, "connect", connect)
        # Skip only server discovery, retaining SQLAlchemy execution and cursor lifecycle.
        monkeypatch.setattr(MySQLDialect_asyncmy, "initialize", lambda *args: None)
        monkeypatch.setattr(MySQLDialect_asyncmy, "on_connect", lambda *args: None)
        monkeypatch.setattr(
            MySQLDialect_asyncmy, "default_isolation_level", "AUTOCOMMIT", raising=False
        )
        routes = ()
    else:
        pod_connection = SimpleNamespace(cursor=lambda: PodCursor(database), close=lambda: None)
        monkeypatch.setitem(
            sys.modules,
            "pymysql",
            SimpleNamespace(
                connect=lambda **kwargs: pod_connection,
                cursors=SimpleNamespace(SSCursor=object),
            ),
        )

        async def execute(access, pod, command, *, stdin, **kwargs):
            output = io.StringIO()
            with monkeypatch.context() as patch, redirect_stdout(output):
                patch.setattr(sys, "stdin", io.StringIO(stdin.decode()))
                exec(command[-1], {})
            await database.pause()
            return output.getvalue().encode()

        monkeypatch.setattr(KubernetesAccess, "execute", execute)
        routes = (PodPythonTransport(KubernetesAccess(None, "test"), "db-client"),)

    async def resolve():
        return MySQLTarget("unused", "user", "secret")

    connection = (
        ConnectionSource("test", resolve, routes) if routes else ConnectionSource("test", resolve)
    )
    source = MySQLDataSource(connection, concurrency=2, max_rows=2, timeout_s=0.2)
    async with ClientManager() as clients:
        yield await clients.get(source), database


async def test_literal_percent_without_parameters(query_client):
    client, database = query_client
    sql = "SELECT DATE_FORMAT(NOW(), '%Y-%m-%d'), '100%'"
    await client.query(sql)
    assert database.statements == [sql]


async def test_bound_parameters_and_escaped_percent(query_client):
    client, database = query_client
    await client.query("SELECT %(value)s, DATE_FORMAT(NOW(), '%%Y')", {"value": "a'b%"})
    assert database.params == {"value": "a'b%"}
    assert database.statements == ["SELECT 'a\\'b%', DATE_FORMAT(NOW(), '%Y')"]


async def test_empty_mapping_still_interpolates_percent(query_client):
    client, database = query_client
    await client.query("SELECT '100%%'", {})
    assert database.statements == ["SELECT '100%'"]


@pytest.mark.parametrize("rows", [[], [(1,)], [(1,), (2,)]])
async def test_select_results_do_not_read_closed_cursor_rowcount(query_client, rows):
    client, database = query_client
    database.rows = rows
    result = await client.query("SELECT value FROM records")
    assert result.columns == ("value",)
    assert result.rows == tuple(rows)
    assert result.affected_rows == -1
    assert result.mappings() == [{"value": row[0]} for row in rows]
    assert database.closed >= 1


async def test_non_row_statement_preserves_affected_rows(query_client):
    client, database = query_client
    database.columns, database.rows = (), []
    result = await client.query("UPDATE records SET value = 1")
    assert result.affected_rows == 3
    assert result.columns == result.rows == ()


async def test_row_limit_closes_cursor_without_retry(query_client):
    client, database = query_client
    database.rows = [(1,), (2,), (3,)]
    with pytest.raises(ValueError, match="row limit"):
        await client.query("SELECT value FROM records")
    assert len(database.statements) == 1
    assert database.closed >= 1


VALUES = [
    None,
    True,
    7,
    1.25,
    "中文",
    b"\x00\xff",
    Decimal("12345678901234567890.123400"),
    datetime(2026, 9, 12, 10, 20, 30, 123456),
    date(2026, 9, 12),
    time(10, 20, 30, 123456),
    timedelta(days=-2, seconds=3, microseconds=4),
]


@pytest.mark.parametrize("value", VALUES)
async def test_results_preserve_native_types_on_both_routes(query_client, value):
    client, database = query_client
    database.rows = [(value,)]
    result = await client.query("SELECT value FROM records")
    actual = result.mappings()[0]["value"]
    assert actual == value
    assert type(actual) is type(value)


@pytest.mark.parametrize("value", VALUES)
async def test_parameters_preserve_native_types_on_both_routes(query_client, value):
    client, database = query_client
    await client.query("SELECT %(value)s", {"value": value})
    assert database.params["value"] == value
    assert type(database.params["value"]) is type(value)


async def test_timeout_does_not_replay(query_client):
    client, database = query_client
    database.delay = 1
    with pytest.raises(TimeoutError):
        await client.query("SELECT 1")
    assert len(database.statements) == 1
    assert database.active == 0


async def test_concurrency_budget_is_shared(query_client):
    client, database = query_client
    database.delay = 0.01
    await asyncio.gather(*(client.query("SELECT 1") for _ in range(6)))
    assert len(database.statements) == 6
    assert database.max_active == 2
    assert database.active == 0
