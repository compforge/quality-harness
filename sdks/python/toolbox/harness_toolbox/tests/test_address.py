from contextlib import asynccontextmanager
from dataclasses import replace
from unittest.mock import AsyncMock

import httpx
import pytest
from harness_common import ClientManager, Environment, KubernetesEnvironment
from kubernetes_asyncio import client as kube_api
from kubernetes_asyncio.client.exceptions import ApiException
from sqlalchemy.exc import OperationalError

from harness_toolbox.address import AddressPolicy, address_candidates
from harness_toolbox.errors import ErrorKind, MySQLConnectionError, OpenSearchRequestError
from harness_toolbox.kube import KubernetesClient, KubernetesDataSource
from harness_toolbox.mysql import MySQLDataSource, MySQLTarget
from harness_toolbox.opensearch import OpenSearchDataSource, OpenSearchTarget
from harness_toolbox.transport import ConnectionSource, Endpoint

ENV = KubernetesEnvironment("smoke", "/configs/smoke", "smoke-context")
POLICY = AddressPolicy(ENV, "runtime", timeout_s=3, connection_pool_maxsize=2)


class ServiceAPI:
    def __init__(self, spec=None, status=None):
        self.spec = spec or kube_api.V1ServiceSpec(cluster_ips=["10.0.0.1", "10.0.0.2"])
        self.status = status
        self.calls = []

    async def read_namespaced_service(self, name, namespace, **kwargs):
        self.calls.append((name, namespace, kwargs))
        if self.status:
            raise ApiException(status=self.status, reason="private server response")
        return kube_api.V1Service(spec=self.spec)

    async def read_namespaced_endpoints(self, name, namespace, **kwargs):
        return kube_api.V1Endpoints(
            subsets=[
                kube_api.V1EndpointSubset(
                    addresses=[kube_api.V1EndpointAddress(ip="10.1.0.1")],
                    not_ready_addresses=[kube_api.V1EndpointAddress(ip="10.1.0.2")],
                )
            ]
        )


def install_kube(monkeypatch, api):
    sources = []

    def create(source, clients):
        sources.append(source)
        return KubernetesClient(source, api=api)

    monkeypatch.setattr(KubernetesDataSource, "create_client", create)
    return sources


@pytest.mark.parametrize(
    "host,namespace",
    [
        ("mysql-primary", "runtime"),
        ("mysql-primary.runtime", "runtime"),
        ("mysql-primary.storage.svc", "storage"),
        ("mysql-primary.storage.svc.cluster.local.", "storage"),
    ],
)
async def test_service_uses_environment_and_never_local_dns(monkeypatch, host, namespace):
    dns = AsyncMock(side_effect=AssertionError("ambient DNS points at another cluster"))
    monkeypatch.setattr("harness_toolbox.address._dns", dns)
    api = ServiceAPI()
    sources = install_kube(monkeypatch, api)
    async with ClientManager() as clients:
        candidates = [
            item async for item in address_candidates(Endpoint(host, 3306), POLICY, clients)
        ]
    assert [item.endpoint.host for item in candidates] == ["10.0.0.1", "10.0.0.2"]
    assert sources[0].kubeconfig == ENV.kubeconfig
    assert sources[0].context_name == ENV.context
    assert sources[0].options.namespace == namespace
    assert sources[0].options.connection_pool_maxsize == 2
    assert api.calls == [("mysql-primary", namespace, {"_request_timeout": 3})]
    dns.assert_not_awaited()


@pytest.mark.parametrize("status", [403, 404])
async def test_service_lookup_failure_never_escapes_environment(monkeypatch, status):
    dns = AsyncMock(side_effect=AssertionError("must not fall back to ambient Service DNS"))
    monkeypatch.setattr("harness_toolbox.address._dns", dns)
    install_kube(monkeypatch, ServiceAPI(status=status))
    async with ClientManager() as clients:
        candidates = [
            item
            async for item in address_candidates(
                Endpoint("mysql-primary", 3306),
                replace(POLICY, fallback_hosts=("10.2.0.1",)),
                clients,
            )
        ]
    assert candidates[0].error.kind == (
        ErrorKind.PERMISSION_DENIED if status == 403 else ErrorKind.RESOURCE_NOT_FOUND
    )
    assert "private" not in str(candidates[0].error)
    assert candidates[1].endpoint.host == "10.2.0.1"
    dns.assert_not_awaited()


async def test_headless_uses_only_ready_addresses(monkeypatch):
    install_kube(monkeypatch, ServiceAPI(kube_api.V1ServiceSpec(cluster_ip="None")))
    async with ClientManager() as clients:
        candidates = [
            item async for item in address_candidates(Endpoint("db", 3306), POLICY, clients)
        ]
    assert [item.endpoint.host for item in candidates] == ["10.1.0.1"]


async def test_domains_and_ip_candidates_preserve_identity_and_order(monkeypatch):
    dns = AsyncMock(return_value=("::1", "127.0.0.1", "::1"))
    monkeypatch.setattr("harness_toolbox.address._dns", dns)
    install_kube(monkeypatch, ServiceAPI(status=403))
    policy = replace(POLICY, fallback_hosts=("127.0.0.1", "192.0.2.1"))
    async with ClientManager() as clients:
        candidates = [
            item async for item in address_candidates(Endpoint("example.com", 443), policy, clients)
        ]
    assert [item.endpoint.host for item in candidates] == ["::1", "127.0.0.1", "192.0.2.1"]
    assert all(item.endpoint.servername == "example.com" for item in candidates)
    dns.assert_awaited_once_with("example.com", 443, 3)


async def test_mysql_tries_next_address_after_unknown_database(monkeypatch):
    install_kube(monkeypatch, ServiceAPI())
    attempts, disposed = [], []

    class Engine:
        def __init__(self, host):
            self.host = host

        @asynccontextmanager
        async def connect(self):
            if self.host == "10.0.0.1":
                raise OperationalError(None, None, Exception(1049, "private DB name"))
            yield self

        async def dispose(self):
            disposed.append(self.host)

    def create(url, **kwargs):
        attempts.append((url.host, url.database))
        return Engine(url.host)

    monkeypatch.setattr("sqlalchemy.ext.asyncio.create_async_engine", create)
    source = MySQLDataSource(
        ConnectionSource(
            "hibot",
            AsyncMock(return_value=MySQLTarget("mysql-primary", "user", "private", "hibot")),
            addresses=POLICY,
        )
    )
    async with ClientManager() as clients:
        client = await clients.get(source)
        assert client.diagnostics["target"]["host"] == "mysql-primary"
        assert client.diagnostics["selected_endpoint"]["host"] == "10.0.0.2"
        assert client.diagnostics["attempts"][0]["error"]["code"] == 1049
        assert await clients.get(source) is client
    assert attempts == [("10.0.0.1", "hibot"), ("10.0.0.2", "hibot")]
    assert disposed == ["10.0.0.1", "10.0.0.2"]


async def test_all_mysql_candidates_fail_with_complete_safe_diagnostics(monkeypatch):
    @asynccontextmanager
    async def connect():
        raise OperationalError(None, None, Exception(1045, "private password"))
        yield

    class Engine:
        def connect(self):
            return connect()

        async def dispose(self):
            pass

    monkeypatch.setattr("sqlalchemy.ext.asyncio.create_async_engine", lambda *a, **kw: Engine())
    source = MySQLDataSource(
        ConnectionSource(
            "db",
            AsyncMock(return_value=MySQLTarget("127.0.0.1", "private-user", "private-password")),
            addresses=AddressPolicy(fallback_hosts=("127.0.0.2",)),
        )
    )
    async with ClientManager() as clients:
        client = source.create_client(clients)
        try:
            with pytest.raises(MySQLConnectionError):
                await client.initialize()
            assert len(client.diagnostics["attempts"]) == 2
            assert "private" not in str(client.diagnostics)
        finally:
            await client.dispose()


async def test_opensearch_address_fallback_keeps_tls_and_host_and_never_replays_query(monkeypatch):
    dns = AsyncMock(return_value=("127.0.0.1", "127.0.0.2"))
    monkeypatch.setattr("harness_toolbox.address._dns", dns)
    requests, closed = [], []

    class HTTPTransport(httpx.MockTransport):
        async def aclose(self):
            closed.append(self)
            await super().aclose()

    async def handle(request):
        requests.append(request)
        status = 200 if request.url.host == "127.0.0.2" and request.method == "HEAD" else 403
        return httpx.Response(status, json={})

    original = httpx.AsyncClient

    class HTTPClient(original):
        def __init__(self, **kwargs):
            assert kwargs["verify"] is not False
            super().__init__(**kwargs, transport=HTTPTransport(handle))

    monkeypatch.setattr(httpx, "AsyncClient", HTTPClient)
    source = OpenSearchDataSource(
        ConnectionSource(
            "search",
            AsyncMock(
                return_value=OpenSearchTarget("https://search.example:9200", "reader", "secret")
            ),
            addresses=AddressPolicy(Environment("test")),
        )
    )
    async with ClientManager() as clients:
        client = await clients.get(source)
        assert client.diagnostics["selected_endpoint"]["host"] == "127.0.0.2"
        with pytest.raises(OpenSearchRequestError):
            await client.request("POST", "/_search", {"query": {"match_all": {}}})
    assert [r.method for r in requests] == ["HEAD", "HEAD", "POST"]
    assert all(r.headers["Host"] == "search.example:9200" for r in requests)
    assert all(r.extensions["sni_hostname"] == "search.example" for r in requests)
    assert all(r.url.scheme == "https" for r in requests)
    assert len(closed) == len(set(closed)) == 2


def test_source_key_includes_context_and_ordered_fallbacks():
    source = MySQLDataSource(ConnectionSource("db", AsyncMock(), addresses=POLICY))
    assert (
        replace(
            source,
            connection=replace(
                source.connection, addresses=replace(POLICY, environment=replace(ENV, name="alias"))
            ),
        ).key
        == source.key
    )
    for policy in [
        replace(POLICY, environment=replace(ENV, context="other")),
        replace(POLICY, fallback_hosts=("10.1.1.1",)),
        replace(POLICY, namespace="other"),
    ]:
        assert (
            replace(source, connection=replace(source.connection, addresses=policy)).key
            != source.key
        )
