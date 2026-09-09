"""AsyncJSONRunner — async sibling of ``JSONRunner``.

For any caller that needs to issue JSON HTTP requests inside an async function.
Produces the same ``Outcome`` shape as the sync runner so all downstream judges
/ extractors / persistence work unchanged.
"""

from __future__ import annotations

import time
from typing import Any

import httpx

from e2e_harness.core.config import E2EConfig
from e2e_harness.runner.async_base import AsyncBaseRunner
from e2e_harness.runner.base import Outcome, Request
from e2e_harness.runner.headers import build_headers


class AsyncJSONRunner(AsyncBaseRunner):
    """Async runner for standard JSON REST/RPC APIs."""

    def __init__(self, config: E2EConfig, *, client: httpx.AsyncClient | None = None):
        self._env = config
        self._client = client or httpx.AsyncClient(
            base_url=config.service.base_url,
            timeout=httpx.Timeout(config.runtime.http_timeout_s),
        )

    async def trigger(self, request: Request) -> Outcome:
        headers = build_headers(
            self._env,
            extra=request.headers,
            exclude=request.exclude_headers,
        )

        start = time.monotonic()
        resp = await self._client.request(
            method=request.method,
            url=request.path,
            json=request.body,
            headers=headers,
            params=request.query or None,
        )
        duration_ms = int((time.monotonic() - start) * 1000)

        return Outcome(
            status_code=resp.status_code,
            body=self._parse_body(resp),
            headers=dict(resp.headers),
            duration_ms=duration_ms,
            metadata={},
            raw=resp.content,
        )

    @staticmethod
    def _parse_body(resp: httpx.Response) -> dict[str, Any] | None:
        ct = resp.headers.get("content-type", "")
        if "json" not in ct:
            return None
        try:
            return resp.json()
        except Exception:
            return None

    async def aclose(self) -> None:
        await self._client.aclose()

    async def __aenter__(self) -> "AsyncJSONRunner":
        return self

    async def __aexit__(self, *_: Any) -> None:
        await self.aclose()
