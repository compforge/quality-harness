"""HTTP pools participate in the common client ownership protocol."""

from dataclasses import dataclass

import httpx
from harness_common.client import _ClientBorrower, client_key


class HTTPClient(httpx.AsyncClient):
    async def initialize(self) -> None:
        """HTTP connections open lazily on the first request."""

    async def dispose(self) -> None:
        await self.aclose()


@dataclass(frozen=True)
class HTTPClientProvider:
    max_connections: int
    timeout_s: float
    pool: str = "default"

    @property
    def client_key(self) -> str:
        return client_key(
            "http",
            {
                "max_connections": self.max_connections,
                "timeout_s": self.timeout_s,
                "pool": self.pool,
            },
        )

    def create_client(self, clients: _ClientBorrower) -> HTTPClient:
        return HTTPClient(
            limits=httpx.Limits(
                max_connections=self.max_connections, max_keepalive_connections=self.max_connections
            ),
            timeout=self.timeout_s,
            http2=False,
            trust_env=False,
        )
