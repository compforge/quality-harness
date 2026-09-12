"""MySQL routes are selected during initialization, never by replaying SQL."""

from __future__ import annotations

import asyncio
import errno
import json
import math
from collections.abc import Mapping
from contextlib import AsyncExitStack
from dataclasses import dataclass, field
from importlib.resources import files
from typing import TYPE_CHECKING

from harness_toolbox._mysql_values import decode_value, encode_value
from harness_toolbox.client import ClientProvider, data_source_key
from harness_toolbox.transport import ConnectionSource, Endpoint, PodPythonTransport

if TYPE_CHECKING:
    from sqlalchemy import Connection
    from sqlalchemy.ext.asyncio import AsyncEngine


@dataclass(frozen=True)
class MySQLTarget:
    host: str
    username: str
    password: str = field(repr=False)
    database: str = ""
    port: int = 3306


@dataclass(frozen=True)
class QueryResult:
    """Native DBAPI values on every route; affected_rows is -1 for row-returning SQL.

    Use len(rows) for the number of collected records. JSON output formatting belongs
    to the caller, not to the choice of database transport.
    """

    columns: tuple[str, ...]
    rows: tuple[tuple[object, ...], ...]
    affected_rows: int

    def mappings(self) -> list[dict[str, object]]:
        return [dict(zip(self.columns, row, strict=True)) for row in self.rows]


@dataclass(frozen=True)
class MySQLDataSource:
    connection: ConnectionSource[MySQLTarget]
    concurrency: int = 4
    timeout_s: float = 60
    connect_timeout_s: float = 10
    max_rows: int = 10000

    @property
    def key(self) -> str:
        return data_source_key(
            "mysql",
            [
                self.connection.key,
                [t.key for t in self.connection.transports],
                self.concurrency,
                self.timeout_s,
                self.connect_timeout_s,
                self.max_rows,
            ],
        )

    def create_client(self, clients: ClientProvider) -> MySQLClient:
        return MySQLClient(self)


# The Pod supplies PyMySQL; configuration and statements are stdin data, not Pod env conventions.
_POD_QUERY = (
    files("harness_toolbox").joinpath("_mysql_values.py").read_text(encoding="utf-8")
    + r"""
import json, sys
import pymysql
p = json.load(sys.stdin)
c = pymysql.connect(**p["connection"], cursorclass=pymysql.cursors.SSCursor, autocommit=True)
try:
    if p["sql"] is None:
        print("{}")
    else:
        with c.cursor() as cursor:
            params = p["params"]
            if params is not None:
                params = {key: decode_value(value) for key, value in params.items()}
            cursor.execute(p["sql"], params)
            columns = [d[0] for d in cursor.description] if cursor.description else []
            rows = cursor.fetchmany(p["max_rows"] + 1) if columns else []
            if len(rows) > p["max_rows"]:
                raise ValueError("query exceeds row limit")
            print(json.dumps({
                "columns": columns,
                "rows": [[encode_value(value) for value in row] for row in rows],
                "affected_rows": -1 if columns else cursor.rowcount,
            }))
finally:
    c.close()
"""
)


def _connection_network_error(error: BaseException) -> bool:
    from sqlalchemy.exc import DBAPIError

    original = error.orig if isinstance(error, DBAPIError) else error
    # This classifier is called only while acquiring a connection, before any user SQL.
    if isinstance(original, OSError) and original.errno in (
        errno.ECONNREFUSED,
        errno.ETIMEDOUT,
        errno.EHOSTUNREACH,
        errno.ENETUNREACH,
        errno.ECONNRESET,
    ):
        return True
    return bool(original.args and original.args[0] in (2002, 2003, 2005))


class MySQLClient:
    def __init__(self, source: MySQLDataSource) -> None:
        if (
            min(source.concurrency, source.timeout_s, source.connect_timeout_s, source.max_rows)
            <= 0
        ):
            raise ValueError("MySQL limits must be positive")
        self._source = source
        self._target: MySQLTarget | None = None
        self._engine: AsyncEngine | None = None
        self._pod: PodPythonTransport | None = None
        self._stack = AsyncExitStack()
        self._slots = asyncio.Semaphore(source.concurrency)
        self._disposed = False
        self._disposal: asyncio.Task[None] | None = None

    async def initialize(self) -> None:
        if self._disposed:
            raise RuntimeError("MySQL client is disposed")
        if self._engine is not None or self._pod is not None:
            return
        self._target = await self._source.connection.resolve()
        target = self._target
        if not self._source.connection.transports:
            raise ValueError("MySQL requires a transport")
        for index, transport in enumerate(self._source.connection.transports):
            stack = AsyncExitStack()
            try:
                if isinstance(transport, PodPythonTransport):
                    await self._pod_query(transport, None, None)
                    self._pod = transport
                    return
                from sqlalchemy import URL
                from sqlalchemy.ext.asyncio import create_async_engine

                mapped = await stack.enter_async_context(
                    transport.connect(Endpoint(target.host, target.port))
                )
                engine = create_async_engine(
                    URL.create(
                        "mysql+asyncmy",
                        username=target.username,
                        password=target.password,
                        host=mapped.host,
                        port=mapped.port,
                        database=target.database,
                    ),
                    pool_size=self._source.concurrency,
                    max_overflow=0,
                    pool_timeout=self._source.timeout_s,
                    connect_args={"connect_timeout": self._source.connect_timeout_s},
                    hide_parameters=True,
                    isolation_level="AUTOCOMMIT",
                )
                stack.push_async_callback(engine.dispose)
                async with asyncio.timeout(self._source.connect_timeout_s), engine.connect():
                    pass
                self._engine = engine
                self._stack = stack
                return
            except BaseException as error:
                await stack.aclose()
                if isinstance(error, asyncio.CancelledError):
                    raise
                if isinstance(transport, PodPythonTransport) or index + 1 == len(
                    self._source.connection.transports
                ):
                    raise
                if not isinstance(error, TimeoutError) and not _connection_network_error(error):
                    raise

    async def query(self, sql: str, params: Mapping[str, object] | None = None) -> QueryResult:
        """Execute once with DBAPI %(name)s bindings; no automatic retry.

        With params=None, SQL is passed literally, including percent signs. With a
        mapping (even an empty one), literal percent signs must be escaped as %%.
        Native decimal, temporal and binary values survive Pod transport unchanged.
        """
        if self._disposed or (self._engine is None and self._pod is None):
            raise RuntimeError("MySQL client is not initialized")
        async with asyncio.timeout(self._source.timeout_s), self._slots:
            if self._pod is not None:
                return await self._pod_query(self._pod, sql, params)
            assert self._engine is not None
            async with self._engine.connect() as connection:

                def execute(sync_connection: Connection) -> QueryResult:
                    # SQLAlchemy otherwise turns None into (), enabling DBAPI %-formatting.
                    result = sync_connection.execution_options(
                        stream_results=True, no_parameters=params is None
                    ).exec_driver_sql(sql, params)
                    try:
                        if not result.returns_rows:
                            return QueryResult((), (), result.rowcount)
                        rows = result.fetchmany(self._source.max_rows + 1)
                        if len(rows) > self._source.max_rows:
                            raise ValueError("query exceeds row limit")
                        # Fetching through EOF can close asyncmy's cursor; SELECT rowcount
                        # is unspecified anyway and must not be read from that cursor.
                        return QueryResult(
                            tuple(result.keys()), tuple(tuple(row) for row in rows), -1
                        )
                    finally:
                        result.close()

                return await connection.run_sync(execute)

    async def _pod_query(
        self, transport: PodPythonTransport, sql: str | None, params: Mapping[str, object] | None
    ) -> QueryResult:
        assert self._target is not None
        target = self._target
        payload = {
            "connection": {
                "host": target.host,
                "port": target.port,
                "user": target.username,
                "password": target.password,
                "database": target.database,
                "connect_timeout": math.ceil(self._source.connect_timeout_s),
                "read_timeout": math.ceil(self._source.timeout_s),
                "write_timeout": math.ceil(self._source.timeout_s),
            },
            "sql": sql,
            "params": None
            if params is None
            else {key: encode_value(value) for key, value in params.items()},
            "max_rows": self._source.max_rows,
        }
        data = json.loads(
            await transport.run(_POD_QUERY, payload, timeout_s=self._source.timeout_s)
        )
        return QueryResult(
            tuple(data.get("columns", [])),
            tuple(tuple(decode_value(value) for value in row) for row in data.get("rows", [])),
            data.get("affected_rows", 0),
        )

    async def dispose(self) -> None:
        if self._disposal is None:
            self._disposed = True
            self._disposal = asyncio.create_task(self._dispose())
        await asyncio.shield(self._disposal)

    async def _dispose(self) -> None:
        await self._stack.aclose()
        self._engine = None
        self._pod = None
