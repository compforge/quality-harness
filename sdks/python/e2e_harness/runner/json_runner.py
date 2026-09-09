"""JSONRunner — synchronous JSON API runner.

Sends HTTP requests and parses JSON responses into Outcome.
"""

from __future__ import annotations

import time
from typing import Any

import httpx

from e2e_harness.core.config import E2EConfig
from e2e_harness.runner.base import BaseRunner, Outcome, Request
from e2e_harness.runner.headers import build_headers


class JSONRunner(BaseRunner):
    """Runner for standard JSON REST/RPC APIs."""

    def __init__(self, config: E2EConfig, *, client: httpx.Client | None = None):
        self._env = config
        self._client = client or httpx.Client(
            base_url=config.service.base_url,
            timeout=httpx.Timeout(config.runtime.http_timeout_s),
        )

    def trigger(self, request: Request) -> Outcome:
        headers = build_headers(
            self._env, extra=request.headers, exclude=request.exclude_headers
        )

        start = time.monotonic()
        resp = self._client.request(
            method=request.method,
            url=request.path,
            json=request.body,
            headers=headers,
            params=request.query or None,
        )
        duration_ms = int((time.monotonic() - start) * 1000)

        body = self._parse_body(resp)

        return Outcome(
            status_code=resp.status_code,
            body=body,
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

    def close(self) -> None:
        self._client.close()

    def __enter__(self) -> JSONRunner:
        return self

    def __exit__(self, *_: Any) -> None:
        self.close()
