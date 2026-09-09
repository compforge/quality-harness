"""Tests for AsyncJSONRunner / AsyncSSERunner + LineBuffer."""

from __future__ import annotations

import asyncio
import threading
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, HTTPServer
from typing import Iterator


from e2e_harness.core.config import E2EConfig, Service
from e2e_harness.runner.async_json_runner import AsyncJSONRunner
from e2e_harness.runner.async_sse_runner import AsyncSSERunner
from e2e_harness.runner.base import Request
from e2e_harness.runner.line_buffer import LineBuffer


# --------------------------------------------------------------------------- LineBuffer


class TestLineBuffer:
    def test_single_line(self):
        buf = LineBuffer()
        assert list(buf.feed(b"hello\n")) == ["hello"]
        assert buf.flush() is None

    def test_split_across_chunks(self):
        buf = LineBuffer()
        assert list(buf.feed(b"hel")) == []
        assert list(buf.feed(b"lo\nwor")) == ["hello"]
        assert list(buf.feed(b"ld\n")) == ["world"]

    def test_multiple_lines_one_chunk(self):
        buf = LineBuffer()
        assert list(buf.feed(b"a\nb\nc\n")) == ["a", "b", "c"]

    def test_crlf_stripped(self):
        buf = LineBuffer()
        assert list(buf.feed(b"hi\r\n")) == ["hi"]

    def test_trailing_via_flush(self):
        buf = LineBuffer()
        list(buf.feed(b"partial"))
        assert buf.flush() == "partial"

    def test_flush_idempotent(self):
        buf = LineBuffer()
        list(buf.feed(b"x\n"))
        assert buf.flush() is None

    def test_empty_chunk_noop(self):
        buf = LineBuffer()
        assert list(buf.feed(b"")) == []
        list(buf.feed(b"a\n"))
        assert list(buf.feed(b"")) == []

    def test_utf8_replacement_on_malformed(self):
        buf = LineBuffer()
        # \xff is invalid UTF-8 start byte; should be replaced not crash
        lines = list(buf.feed(b"a\xffb\n"))
        assert len(lines) == 1
        assert lines[0].startswith("a") and lines[0].endswith("b")


# --------------------------------------------------------------------------- HTTP mocks


class _JSONHandler(BaseHTTPRequestHandler):
    captured: dict = {}
    response_body: bytes = b'{"ok": true}'

    def do_POST(self):  # noqa: N802
        cl = int(self.headers.get("Content-Length", 0))
        body = self.rfile.read(cl) if cl else b""
        _JSONHandler.captured = {
            "method": self.command,
            "path": self.path,
            "headers": {k.lower(): v for k, v in self.headers.items()},
            "body": body,
        }
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(self.response_body)

    def do_GET(self):  # noqa: N802
        self.do_POST()

    def log_message(self, *_):  # noqa
        pass


class _SSEHandler(BaseHTTPRequestHandler):
    sse_body: bytes = b""
    captured_headers: dict = {}

    def do_POST(self):  # noqa: N802
        _SSEHandler.captured_headers = {k.lower(): v for k, v in self.headers.items()}
        cl = int(self.headers.get("Content-Length", 0))
        if cl:
            self.rfile.read(cl)
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.end_headers()
        self.wfile.write(self.sse_body)

    def log_message(self, *_):  # noqa
        pass


@contextmanager
def _serve(handler_cls) -> Iterator[str]:
    server = HTTPServer(("127.0.0.1", 0), handler_cls)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_port}"
    finally:
        server.shutdown()
        thread.join(timeout=2)


def _env(base_url: str) -> E2EConfig:
    return E2EConfig(
        service=Service(
            name="t", base_url=base_url, headers={"X-T": "ten", "X-U": "usr"}
        ),
    )


# --------------------------------------------------------------------------- AsyncJSONRunner


class TestAsyncJSONRunner:
    def test_post_returns_outcome(self):
        _JSONHandler.response_body = b'{"id": "abc-123"}'

        async def go():
            with _serve(_JSONHandler) as base_url:
                async with AsyncJSONRunner(_env(base_url)) as r:
                    return await r.trigger(
                        Request(
                            method="POST",
                            path="/api/v1/things",
                            body={"name": "test"},
                        )
                    )

        outcome = asyncio.run(go())
        assert outcome.status_code == 200
        assert outcome.body == {"id": "abc-123"}
        assert outcome.duration_ms >= 0
        assert outcome.raw == b'{"id": "abc-123"}'

    def test_service_headers_injected(self):
        _JSONHandler.response_body = b"{}"

        async def go():
            with _serve(_JSONHandler) as base_url:
                async with AsyncJSONRunner(_env(base_url)) as r:
                    await r.trigger(Request(method="POST", path="/x"))

        asyncio.run(go())
        assert _JSONHandler.captured["headers"]["x-t"] == "ten"
        assert _JSONHandler.captured["headers"]["x-u"] == "usr"

    def test_exclude_headers_drops_auth(self):
        _JSONHandler.response_body = b"{}"

        async def go():
            with _serve(_JSONHandler) as base_url:
                async with AsyncJSONRunner(_env(base_url)) as r:
                    await r.trigger(
                        Request(
                            method="POST",
                            path="/x",
                            exclude_headers={"X-T"},
                        )
                    )

        asyncio.run(go())
        assert "x-t" not in _JSONHandler.captured["headers"]
        assert _JSONHandler.captured["headers"]["x-u"] == "usr"

    def test_non_json_response_body_is_none(self):
        async def go():
            class _PlainHandler(_JSONHandler):
                def do_POST(self):  # noqa: N802
                    self.send_response(200)
                    self.send_header("Content-Type", "text/plain")
                    self.end_headers()
                    self.wfile.write(b"hi")

            with _serve(_PlainHandler) as base_url:
                async with AsyncJSONRunner(_env(base_url)) as r:
                    return await r.trigger(Request(method="POST", path="/x"))

        outcome = asyncio.run(go())
        assert outcome.body is None
        assert outcome.raw == b"hi"


# --------------------------------------------------------------------------- AsyncSSERunner


class TestAsyncSSERunner:
    def test_consumes_stream(self):
        _SSEHandler.sse_body = (
            b'data: {"op":"start","id":"msg-1"}\n\n'
            b'data: {"op":"delta","t":"hello"}\n\n'
            b'data: {"op":"done"}\n\n'
        )

        async def go():
            with _serve(_SSEHandler) as base_url:
                async with AsyncSSERunner(_env(base_url)) as r:
                    return await r.trigger(
                        Request(
                            method="POST",
                            path="/chat",
                            body={"q": "hi"},
                        )
                    )

        outcome = asyncio.run(go())
        assert outcome.status_code == 200
        assert outcome.metadata["event_count"] == 3
        evs = outcome.metadata["events"]
        assert evs[0]["data"] == {"op": "start", "id": "msg-1"}
        assert evs[2]["data"] == {"op": "done"}

    def test_accept_header_set(self):
        _SSEHandler.sse_body = b"data: {}\n\n"

        async def go():
            with _serve(_SSEHandler) as base_url:
                async with AsyncSSERunner(_env(base_url)) as r:
                    await r.trigger(Request(method="POST", path="/", body={}))

        asyncio.run(go())
        assert "text/event-stream" in _SSEHandler.captured_headers["accept"]

    def test_on_event_callback_sync(self):
        _SSEHandler.sse_body = b'data: {"op":"a"}\n\ndata: {"op":"b"}\n\n'
        seen: list[str] = []

        def cb(e):
            d = e.json() or {}
            seen.append(d.get("op", ""))

        async def go():
            with _serve(_SSEHandler) as base_url:
                async with AsyncSSERunner(_env(base_url), on_event=cb) as r:
                    await r.trigger(Request(method="POST", path="/", body={}))

        asyncio.run(go())
        assert seen == ["a", "b"]

    def test_on_event_callback_async(self):
        _SSEHandler.sse_body = b'data: {"op":"a"}\n\ndata: {"op":"b"}\n\n'
        seen: list[str] = []

        async def cb(e):
            d = e.json() or {}
            seen.append(d.get("op", ""))

        async def go():
            with _serve(_SSEHandler) as base_url:
                async with AsyncSSERunner(_env(base_url), on_event=cb) as r:
                    await r.trigger(Request(method="POST", path="/", body={}))

        asyncio.run(go())
        assert seen == ["a", "b"]

    def test_event_helpers_work_on_async_outcome(self):
        """Outcome from AsyncSSERunner should plug into events helpers identically."""
        from e2e_harness.runner.events import collect_text, find_event_data

        _SSEHandler.sse_body = (
            b'data: {"op":"start","id":"x"}\n\n'
            b'data: {"op":"text_delta","delta":"Hel"}\n\n'
            b'data: {"op":"text_delta","delta":"lo"}\n\n'
        )

        async def go():
            with _serve(_SSEHandler) as base_url:
                async with AsyncSSERunner(_env(base_url)) as r:
                    return await r.trigger(Request(method="POST", path="/", body={}))

        outcome = asyncio.run(go())
        assert find_event_data(outcome, op="start") == {"op": "start", "id": "x"}
        assert collect_text(outcome) == "Hello"

    def test_raw_bytes_preserved(self):
        body = b'event: x\ndata: {"a":1}\n\n'
        _SSEHandler.sse_body = body

        async def go():
            with _serve(_SSEHandler) as base_url:
                async with AsyncSSERunner(_env(base_url)) as r:
                    return await r.trigger(Request(method="POST", path="/", body={}))

        outcome = asyncio.run(go())
        assert outcome.raw == body
