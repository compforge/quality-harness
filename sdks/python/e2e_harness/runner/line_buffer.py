"""Incremental byte → text-line splitter.

Shared by the sync and async SSE runners: each runner reads bytes off the
wire chunk-by-chunk and pushes them into a ``LineBuffer``; the buffer yields
complete lines as soon as a ``\\n`` is seen. Call ``flush`` at stream end to
drain a trailing line not followed by a newline.

Handles CRLF transparently (strips the trailing ``\\r``) and decodes UTF-8
with replacement for malformed bytes (SSE wire is text and recoverable).
"""

from __future__ import annotations

from typing import Iterator


class LineBuffer:
    """Stateful byte accumulator that yields decoded text lines on ``\\n``."""

    __slots__ = ("_buf",)

    def __init__(self) -> None:
        self._buf = b""

    def feed(self, chunk: bytes) -> Iterator[str]:
        """Push bytes; yield each complete line as the buffer crosses a newline."""
        if not chunk:
            return
        self._buf += chunk
        while True:
            nl = self._buf.find(b"\n")
            if nl < 0:
                break
            line = self._buf[:nl]
            self._buf = self._buf[nl + 1 :]
            if line.endswith(b"\r"):
                line = line[:-1]
            yield line.decode("utf-8", errors="replace")

    def flush(self) -> str | None:
        """Return any trailing bytes that did not end with ``\\n``, or ``None``."""
        if not self._buf:
            return None
        line = self._buf
        self._buf = b""
        if line.endswith(b"\r"):
            line = line[:-1]
        return line.decode("utf-8", errors="replace")
