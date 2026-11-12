"""HTTP pools borrow execution-scoped Toolbox transports."""

from dataclasses import dataclass, field
from typing import Protocol

import httpx
from harness_common.access.client import ClientProvider, _ClientBorrower, client_key

from harness_toolbox.socks import SocksProxy
from harness_toolbox.transport import Transport


class HTTPRoute(Transport, Protocol):
    async def initialize(self) -> None: ...
    async def dispose(self) -> None: ...


class HTTPClient(httpx.AsyncClient):
    """Native HTTP streaming/pooling; route and TLS identity remain independent.

    @rule Clients close before their borrowed Transport. No request replay or direct fallback.
    @link component://docs/toolbox.md
    """

    def __init__(
        self,
        *,
        route: ClientProvider[HTTPRoute] | None = None,
        clients: _ClientBorrower | None = None,
        **kwargs,
    ):
        self._route_provider = route
        self._clients = clients
        self._proxy: SocksProxy | None = None
        self._client_options = kwargs
        self._initialized = False
        if route is None:
            super().__init__(**kwargs)
            self._initialized = True

    async def initialize(self) -> None:
        if self._route_provider is None:
            return
        if self._clients is None:
            raise RuntimeError("Routed HTTP requires a ClientManager")
        route = await self._clients.get(self._route_provider)
        self._proxy = SocksProxy(route)
        try:
            await self._proxy.__aenter__()
            super().__init__(proxy=self._proxy.url, **self._client_options)
            self._initialized = True
        except BaseException:
            await self.dispose()
            raise

    async def dispose(self) -> None:
        try:
            if self._initialized:
                await super().aclose()
        finally:
            if self._proxy is not None:
                await self._proxy.__aexit__(None, None, None)
                self._proxy = None


@dataclass(frozen=True)
class HTTPClientProvider:
    max_connections: int
    timeout_s: float
    pool: str = "default"
    transport: ClientProvider[HTTPRoute] | None = field(default=None, repr=False)

    @property
    def client_key(self) -> str:
        return client_key(
            "http",
            {
                "max_connections": self.max_connections,
                "timeout_s": self.timeout_s,
                "pool": self.pool,
                "transport": self.transport.client_key if self.transport else "direct",
            },
        )

    def create_client(self, clients: _ClientBorrower) -> HTTPClient:
        return HTTPClient(
            route=self.transport,
            clients=clients,
            limits=httpx.Limits(
                max_connections=self.max_connections, max_keepalive_connections=self.max_connections
            ),
            timeout=self.timeout_s,
            http2=False,
            trust_env=False,
        )
