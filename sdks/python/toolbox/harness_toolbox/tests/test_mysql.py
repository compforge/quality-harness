from contextlib import asynccontextmanager

import pytest
from sqlalchemy.exc import OperationalError

from harness_toolbox import ClientManager
from harness_toolbox.mysql import (
    MySQLDataSource,
    MySQLTarget,
    QueryResult,
    _connection_network_error,
)
from harness_toolbox.transport import ConnectionSource, DirectTransport


class Route(DirectTransport):
    opened = 0
    closed = 0

    @asynccontextmanager
    async def connect(self, endpoint):
        type(self).opened += 1
        try:
            yield endpoint
        finally:
            type(self).closed += 1


async def test_connection_fallback_but_never_query_replay(monkeypatch):
    Route.opened = Route.closed = 0
    attempts, queries, disposed = [], [], []

    class Engine:
        def __init__(self, attempt):
            self.attempt = attempt

        @asynccontextmanager
        async def connect(self):
            if self.attempt == 0:
                raise OperationalError(None, None, Exception(2003, "connection refused"))
            yield self

        async def run_sync(self, operation):
            queries.append(self.attempt)
            raise OperationalError(None, None, Exception(2013, "connection lost executing SQL"))

        async def dispose(self):
            disposed.append(self.attempt)

    def create(*args, **kwargs):
        engine = Engine(len(attempts))
        attempts.append(engine)
        return engine

    monkeypatch.setattr("sqlalchemy.ext.asyncio.create_async_engine", create)

    async def resolve():
        return MySQLTarget("db", "user", "secret")

    source = MySQLDataSource(ConnectionSource("db", resolve, (Route(), Route())))
    async with ClientManager() as clients:
        client = await clients.get(source)
        with pytest.raises(OperationalError):
            await client.query("UPDATE test SET count = count + 1")
    assert len(attempts) == 2
    assert queries == [1]
    assert disposed == [0, 1]
    assert Route.opened == Route.closed == 2


def test_authentication_and_sql_errors_are_not_network_failures():
    assert not _connection_network_error(OperationalError(None, None, Exception(1045, "denied")))
    assert not _connection_network_error(OperationalError(None, None, Exception(1064, "syntax")))
    assert QueryResult(("a",), ((None,),), 1).mappings() == [{"a": None}]
