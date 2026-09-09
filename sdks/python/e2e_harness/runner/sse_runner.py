"""SSERunner — Server-Sent Events streaming runner.

Sends an HTTP request, parses the SSE response stream via the shared
``SSEParser``, and exposes collected events via ``Outcome.metadata["events"]``.
The runner consumes the full stream before returning (sync), which is
appropriate for short-to-medium streams like chat completions (~seconds to
minutes). For longer streams where partial results matter, consume the events
list incrementally via the ``on_event`` callback.

Outcome shape:
    status_code    HTTP status of the initial response
    headers        response headers (including content-type)
    duration_ms    wall-clock time from request to end-of-stream
    body           None — SSE has no single JSON body; use ``metadata['events']``
    metadata       {
                     "events":      list[dict],  # parsed events (see SSEEvent.to_dict)
                     "event_count": int,
                   }
    raw            full raw response bytes

The line/event parser lives in ``runner/sse_parser.py`` so async streaming
callers can reuse the same state machine without re-implementing it.
"""

from __future__ import annotations

import time
from typing import Any, Callable, Iterator

import httpx

from e2e_harness.core.config import E2EConfig
from e2e_harness.runner.base import BaseRunner, Outcome, Request
from e2e_harness.runner.headers import build_headers
from e2e_harness.runner.line_buffer import LineBuffer
from e2e_harness.runner.sse_parser import SSEEvent, SSEParser


class SSERunner(BaseRunner):
    """Runner for SSE streaming endpoints. Pass ``on_event`` to react inline."""

    def __init__(
        self,
        config: E2EConfig,
        *,
        client: httpx.Client | None = None,
        read_timeout_s: float | None = None,
        on_event: Callable[[SSEEvent], None] | None = None,
    ):
        self._env = config
        # SSE responses are long-lived; allow a separate read timeout decoupled
        # from connect timeout. None → use config.runtime.http_timeout_s.
        read_to = (
            read_timeout_s
            if read_timeout_s is not None
            else float(config.runtime.http_timeout_s)
        )
        self._client = client or httpx.Client(
            base_url=config.service.base_url,
            timeout=httpx.Timeout(connect=10.0, read=read_to, write=10.0, pool=10.0),
        )
        self._on_event = on_event

    def trigger(self, request: Request) -> Outcome:
        headers = build_headers(
            self._env, extra=request.headers, exclude=request.exclude_headers
        )
        headers.setdefault("Accept", "text/event-stream")

        events: list[SSEEvent] = []
        raw_chunks: list[bytes] = []
        start = time.monotonic()

        with self._client.stream(
            method=request.method,
            url=request.path,
            json=request.body,
            headers=headers,
            params=request.query or None,
        ) as resp:
            status_code = resp.status_code
            resp_headers = dict(resp.headers)
            for event in _iter_sse_events(_iter_lines_with_capture(resp, raw_chunks)):
                events.append(event)
                if self._on_event is not None:
                    self._on_event(event)

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

    def close(self) -> None:
        self._client.close()

    def __enter__(self) -> "SSERunner":
        return self

    def __exit__(self, *_: Any) -> None:
        self.close()


def _iter_lines_with_capture(
    resp: httpx.Response,
    sink: list[bytes],
) -> Iterator[str]:
    """Yield decoded text lines while accumulating raw bytes into ``sink``.

    httpx's ``iter_lines()`` drops the original bytes, so we go through
    ``iter_bytes()`` and feed a ``LineBuffer``. This keeps raw bytes available
    for ``Outcome.raw`` (debugging / replay).
    """
    buf = LineBuffer()
    for chunk in resp.iter_bytes():
        if not chunk:
            continue
        sink.append(chunk)
        yield from buf.feed(chunk)
    trailing = buf.flush()
    if trailing is not None:
        yield trailing


def _iter_sse_events(lines: Iterator[str]) -> Iterator[SSEEvent]:
    """Drive SSEParser over a line iterator. See sse_parser for the state machine."""
    parser = SSEParser()
    for line in lines:
        evt = parser.feed_line(line)
        if evt is not None:
            yield evt
    evt = parser.flush()
    if evt is not None:
        yield evt
