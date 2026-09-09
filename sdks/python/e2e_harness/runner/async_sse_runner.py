"""AsyncSSERunner — async sibling of ``SSERunner``.

Shares ``SSEParser`` + ``LineBuffer`` with the sync runner so both produce
identically shaped ``Outcome`` (events list in ``metadata['events']`` plus
``event_count``). Used by async eval pipelines / agent runners.

For early-stop or inline reactions (logging, scoring on-the-fly), pass
``on_event`` — a callable invoked once per parsed event. If ``on_event`` is
async itself, await it via a wrapper.
"""

from __future__ import annotations

import time
from typing import Any, Awaitable, Callable

import httpx

from e2e_harness.core.config import E2EConfig
from e2e_harness.runner.async_base import AsyncBaseRunner
from e2e_harness.runner.base import Outcome, Request
from e2e_harness.runner.headers import build_headers
from e2e_harness.runner.line_buffer import LineBuffer
from e2e_harness.runner.sse_parser import SSEEvent, SSEParser

OnEvent = Callable[[SSEEvent], None] | Callable[[SSEEvent], Awaitable[None]]


class AsyncSSERunner(AsyncBaseRunner):
    """Async runner for SSE streaming endpoints."""

    def __init__(
        self,
        config: E2EConfig,
        *,
        client: httpx.AsyncClient | None = None,
        read_timeout_s: float | None = None,
        on_event: OnEvent | None = None,
    ):
        self._env = config
        read_to = (
            read_timeout_s
            if read_timeout_s is not None
            else float(config.runtime.http_timeout_s)
        )
        self._client = client or httpx.AsyncClient(
            base_url=config.service.base_url,
            timeout=httpx.Timeout(connect=10.0, read=read_to, write=10.0, pool=10.0),
        )
        self._on_event = on_event

    async def trigger(self, request: Request) -> Outcome:
        headers = build_headers(
            self._env,
            extra=request.headers,
            exclude=request.exclude_headers,
        )
        headers.setdefault("Accept", "text/event-stream")

        events: list[SSEEvent] = []
        raw_chunks: list[bytes] = []
        line_buf = LineBuffer()
        parser = SSEParser()
        start = time.monotonic()

        async with self._client.stream(
            method=request.method,
            url=request.path,
            json=request.body,
            headers=headers,
            params=request.query or None,
        ) as resp:
            status_code = resp.status_code
            resp_headers = dict(resp.headers)
            async for chunk in resp.aiter_bytes():
                if not chunk:
                    continue
                raw_chunks.append(chunk)
                for line in line_buf.feed(chunk):
                    evt = parser.feed_line(line)
                    if evt is not None:
                        events.append(evt)
                        await self._dispatch(evt)
            trailing = line_buf.flush()
            if trailing is not None:
                evt = parser.feed_line(trailing)
                if evt is not None:
                    events.append(evt)
                    await self._dispatch(evt)
            final = parser.flush()
            if final is not None:
                events.append(final)
                await self._dispatch(final)

        duration_ms = int((time.monotonic() - start) * 1000)
        return Outcome(
            status_code=status_code,
            body=None,
            headers=resp_headers,
            duration_ms=duration_ms,
            metadata={
                "events": [e.to_dict() for e in events],
                "event_count": len(events),
            },
            raw=b"".join(raw_chunks),
        )

    async def _dispatch(self, event: SSEEvent) -> None:
        if self._on_event is None:
            return
        ret = self._on_event(event)
        if ret is not None and hasattr(ret, "__await__"):
            await ret  # type: ignore[misc]

    async def aclose(self) -> None:
        await self._client.aclose()

    async def __aenter__(self) -> "AsyncSSERunner":
        return self

    async def __aexit__(self, *_: Any) -> None:
        await self.aclose()
