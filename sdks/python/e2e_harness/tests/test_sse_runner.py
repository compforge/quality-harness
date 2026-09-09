"""Tests for e2e_harness.runner.sse_runner."""

from __future__ import annotations

import threading
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, HTTPServer
from typing import Iterator


from e2e_harness.core.config import E2EConfig, Service
from e2e_harness.runner.base import Request
from e2e_harness.runner.sse_runner import SSEEvent, SSERunner, _iter_sse_events


class TestSSEParser:
    """Pure parser tests — no network."""

    def _events(self, raw: str) -> list[SSEEvent]:
        return list(_iter_sse_events(iter(raw.split("\n"))))

    def test_single_event_default_name(self):
        evts = self._events('data: {"k":1}\n\n')
        assert len(evts) == 1
        assert evts[0].event == "message"
        assert evts[0].json() == {"k": 1}

    def test_named_event(self):
        evts = self._events('event: start\ndata: {"msg":"hi"}\n\n')
        assert len(evts) == 1
        assert evts[0].event == "start"
        assert evts[0].json() == {"msg": "hi"}

    def test_event_with_id(self):
        evts = self._events("event: x\nid: evt-1\ndata: payload\n\n")
        assert evts[0].id == "evt-1"
        assert evts[0].data == "payload"

    def test_multiline_data_joined_with_newline(self):
        evts = self._events("data: line1\ndata: line2\n\n")
        assert evts[0].data == "line1\nline2"

    def test_comment_lines_ignored(self):
        evts = self._events(": this is a heartbeat\ndata: hello\n\n")
        assert len(evts) == 1
        assert evts[0].data == "hello"

    def test_blank_field_without_value(self):
        evts = self._events("data\n\n")
        assert len(evts) == 1
        assert evts[0].data == ""

    def test_event_resets_between_events(self):
        raw = "event: a\ndata: 1\n\nevent: b\ndata: 2\n\ndata: 3\n\n"
        evts = self._events(raw)
        assert [(e.event, e.data) for e in evts] == [
            ("a", "1"),
            ("b", "2"),
            ("message", "3"),
        ]

    def test_trailing_event_without_blank_line(self):
        evts = self._events("data: trailing")
        assert len(evts) == 1
        assert evts[0].data == "trailing"

    def test_non_json_data_returns_none_from_json(self):
        evts = self._events("data: not json\n\n")
        assert evts[0].json() is None
        assert evts[0].to_dict()["data"] == "not json"

    def test_to_dict_includes_id_when_present(self):
        e = SSEEvent(event="ev", data="x", id="i-1")
        d = e.to_dict()
        assert d == {"event": "ev", "data": "x", "id": "i-1"}

    def test_to_dict_omits_id_when_empty(self):
        e = SSEEvent(event="ev", data="x")
        d = e.to_dict()
        assert "id" not in d


class _SSEHandler(BaseHTTPRequestHandler):
    """Minimal HTTP handler that returns a fixed SSE stream."""

    sse_body: bytes = b""
    captured_headers: dict[str, str] = {}
    captured_path: str = ""

    def do_POST(self):  # noqa: N802
        _SSEHandler.captured_headers = {k.lower(): v for k, v in self.headers.items()}
        _SSEHandler.captured_path = self.path
        # Drain request body
        cl = int(self.headers.get("Content-Length", 0))
        if cl:
            self.rfile.read(cl)
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.send_header("Cache-Control", "no-cache")
        self.end_headers()
        self.wfile.write(self.sse_body)

    def log_message(self, *_):  # noqa
        pass


@contextmanager
def _serve(sse_body: bytes) -> Iterator[str]:
    _SSEHandler.sse_body = sse_body
    _SSEHandler.captured_headers = {}
    _SSEHandler.captured_path = ""
    server = HTTPServer(("127.0.0.1", 0), _SSEHandler)
    port = server.server_port
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{port}"
    finally:
        server.shutdown()
        thread.join(timeout=2)


def _env(base_url: str) -> E2EConfig:
    return E2EConfig(
        service=Service(
            name="t", base_url=base_url, headers={"X-T": "ten", "X-U": "usr"}
        ),
    )


class TestSSERunner:
    def test_consumes_stream_and_collects_events(self):
        sse_body = (
            b'data: {"op":"start","id":"msg-1"}\n\n'
            b'data: {"op":"delta","t":"hello"}\n\n'
            b'data: {"op":"done"}\n\n'
        )
        with _serve(sse_body) as base_url:
            with SSERunner(_env(base_url)) as r:
                outcome = r.trigger(
                    Request(method="POST", path="/chat", body={"q": "hi"})
                )

        assert outcome.status_code == 200
        assert outcome.body is None
        assert outcome.metadata["event_count"] == 3
        events = outcome.metadata["events"]
        assert events[0]["data"] == {"op": "start", "id": "msg-1"}
        assert events[1]["data"] == {"op": "delta", "t": "hello"}
        assert events[2]["data"] == {"op": "done"}

    def test_service_headers_injected(self):
        with _serve(b"data: {}\n\n") as base_url:
            with SSERunner(_env(base_url)) as r:
                r.trigger(Request(method="POST", path="/chat", body={}))
        assert _SSEHandler.captured_headers.get("x-t") == "ten"
        assert _SSEHandler.captured_headers.get("x-u") == "usr"
        assert "text/event-stream" in _SSEHandler.captured_headers.get("accept", "")

    def test_exclude_headers_drops_auth(self):
        with _serve(b"data: {}\n\n") as base_url:
            with SSERunner(_env(base_url)) as r:
                r.trigger(
                    Request(
                        method="POST",
                        path="/chat",
                        body={},
                        exclude_headers={"X-T"},
                    )
                )
        assert "x-t" not in _SSEHandler.captured_headers
        assert _SSEHandler.captured_headers.get("x-u") == "usr"

    def test_raw_bytes_preserved(self):
        sse_body = b'event: start\ndata: {"a":1}\n\n'
        with _serve(sse_body) as base_url:
            with SSERunner(_env(base_url)) as r:
                outcome = r.trigger(Request(method="POST", path="/chat", body={}))
        assert outcome.raw == sse_body

    def test_on_event_callback_invoked_per_event(self):
        sse_body = b'data: {"op":"a"}\n\n' b'data: {"op":"b"}\n\n'
        seen: list[str] = []

        def cb(e: SSEEvent):
            d = e.json() or {}
            seen.append(d.get("op", ""))

        with _serve(sse_body) as base_url:
            with SSERunner(_env(base_url), on_event=cb) as r:
                r.trigger(Request(method="POST", path="/x", body={}))
        assert seen == ["a", "b"]

    def test_query_params_passed(self):
        with _serve(b"data: {}\n\n") as base_url:
            with SSERunner(_env(base_url)) as r:
                r.trigger(
                    Request(
                        method="POST",
                        path="/",
                        body={},
                        query={"Action": "Chat", "Version": "2025-06-01"},
                    )
                )
        assert "Action=Chat" in _SSEHandler.captured_path
        assert "Version=2025-06-01" in _SSEHandler.captured_path

    def test_empty_stream(self):
        with _serve(b"") as base_url:
            with SSERunner(_env(base_url)) as r:
                outcome = r.trigger(Request(method="POST", path="/", body={}))
        assert outcome.metadata["event_count"] == 0
        assert outcome.metadata["events"] == []
