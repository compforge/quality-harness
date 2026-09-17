"""Streaming SSE observations with bounded event buffering and no business judgment."""

import time
from collections.abc import Callable
from dataclasses import dataclass, field

import httpx

from harness_toolbox.line_buffer import LineBuffer
from harness_toolbox.sse_parser import SSEParser


@dataclass
class SSEObservation:
    status: int | None
    duration_ms: float
    events: int = 0
    nbytes: int = 0
    metrics: dict[str, float] = field(default_factory=dict)
    meta: dict = field(default_factory=dict)


async def stream_sse(
    client: httpx.AsyncClient,
    url: str,
    *,
    json: dict | None = None,
    headers: dict[str, str] | None = None,
    timeout: float = 120,
    done_marker: bytes | None = None,
    on_frame: Callable[[float, bytes], None] | None = None,
    max_event_bytes: int = 1048576,
) -> SSEObservation:
    if max_event_bytes <= 0:
        raise ValueError("max_event_bytes must be positive")
    started = time.monotonic()
    result = SSEObservation(None, 0)
    parser, lines = SSEParser(), LineBuffer()
    buffered = 0

    def line(value: str) -> None:
        nonlocal buffered
        buffered += len(value.encode("utf-8")) + 1
        if buffered > max_event_bytes:
            raise ValueError("SSE event exceeds max_event_bytes")
        event = parser.feed_line(value)
        if not value:
            buffered = 0
        if event is not None:
            data = event.data.encode("utf-8")
            result.events += 1
            if done_marker is not None and done_marker in data:
                result.meta["saw_done"] = True
            if on_frame:
                on_frame((time.monotonic() - started) * 1000, data)

    if done_marker is not None:
        result.meta["saw_done"] = False
    try:
        async with client.stream(
            "POST", url, json=json, headers=headers, timeout=timeout
        ) as response:
            result.status = response.status_code
            async for chunk in response.aiter_bytes():
                if not chunk:
                    continue
                if not result.nbytes:
                    result.metrics["first_byte_ms"] = (time.monotonic() - started) * 1000
                result.nbytes += len(chunk)
                # Bound bytes before handing them to a parser that retains partial lines.
                for offset in range(0, len(chunk), min(65536, max_event_bytes)):
                    piece = chunk[offset : offset + min(65536, max_event_bytes)]
                    for value in lines.feed(piece):
                        line(value)
                    if lines.pending_bytes + buffered > max_event_bytes:
                        raise ValueError("SSE event exceeds max_event_bytes")
            trailing = lines.flush()
            if trailing is not None:
                line(trailing)
            line("")
    except Exception as error:
        result.meta.update(exc=type(error).__name__, exc_detail=str(error))
    result.duration_ms = (time.monotonic() - started) * 1000
    return result
