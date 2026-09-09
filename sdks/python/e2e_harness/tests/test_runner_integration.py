"""Full HTTP integration tests for JSONRunner (real socket)."""

from __future__ import annotations

import json
import threading
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, HTTPServer
from typing import Iterator


from e2e_harness.core.config import E2EConfig, Service
from e2e_harness.runner.base import Request
from e2e_harness.runner.json_runner import JSONRunner


class _Handler(BaseHTTPRequestHandler):
    """Mock HTTP server: dispatches by URL path → returns canned responses."""

    captured: list[dict] = []
    response_status: int = 200
    response_body: bytes = b'{"ok": true}'
    response_content_type: str = "application/json"

    def _record(self, method: str):
        cl = int(self.headers.get("Content-Length", 0))
        body = self.rfile.read(cl) if cl else b""
        _Handler.captured.append(
            {
                "method": method,
                "path": self.path,
                "headers": {k.lower(): v for k, v in self.headers.items()},
                "body": body.decode("utf-8") if body else "",
            }
        )
        self.send_response(self.response_status)
        self.send_header("Content-Type", self.response_content_type)
        self.end_headers()
        self.wfile.write(self.response_body)

    def do_GET(self):  # noqa: N802
        self._record("GET")

    def do_POST(self):  # noqa: N802
        self._record("POST")

    def do_PUT(self):  # noqa: N802
        self._record("PUT")

    def do_DELETE(self):  # noqa: N802
        self._record("DELETE")

    def log_message(self, *_):  # noqa
        pass


@contextmanager
def _serve(
    *,
    body: bytes = b'{"ok": true}',
    status: int = 200,
    content_type: str = "application/json",
) -> Iterator[str]:
    _Handler.captured = []
    _Handler.response_status = status
    _Handler.response_body = body
    _Handler.response_content_type = content_type
    server = HTTPServer(("127.0.0.1", 0), _Handler)
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


# --------------------------------------------------------------------------- JSONRunner


class TestJSONRunnerIntegration:
    def test_post_with_body_and_query(self):
        with _serve(body=b'{"id": "abc"}') as base_url:
            with JSONRunner(_env(base_url)) as r:
                outcome = r.trigger(
                    Request(
                        method="POST",
                        path="/items",
                        body={"name": "x"},
                        query={"validate": "true"},
                    )
                )
        assert outcome.status_code == 200
        assert outcome.body == {"id": "abc"}
        cap = _Handler.captured[0]
        assert cap["method"] == "POST"
        assert "/items?validate=true" == cap["path"]
        assert json.loads(cap["body"]) == {"name": "x"}
        assert cap["headers"]["x-t"] == "ten"

    def test_get_no_body(self):
        with _serve(body=b'{"items": []}') as base_url:
            with JSONRunner(_env(base_url)) as r:
                outcome = r.trigger(Request(method="GET", path="/items"))
        assert outcome.status_code == 200
        assert outcome.body == {"items": []}
        assert _Handler.captured[0]["method"] == "GET"

    def test_extra_request_headers_override_auth(self):
        with _serve() as base_url:
            with JSONRunner(_env(base_url)) as r:
                r.trigger(
                    Request(
                        method="POST",
                        path="/x",
                        body={},
                        headers={"X-T": "override"},
                    )
                )
        assert _Handler.captured[0]["headers"]["x-t"] == "override"

    def test_exclude_headers_drops_auth(self):
        with _serve() as base_url:
            with JSONRunner(_env(base_url)) as r:
                r.trigger(
                    Request(
                        method="POST",
                        path="/x",
                        body={},
                        exclude_headers={"X-T"},
                    )
                )
        assert "x-t" not in _Handler.captured[0]["headers"]
        assert _Handler.captured[0]["headers"]["x-u"] == "usr"

    def test_non_json_response_body_is_none(self):
        with _serve(body=b"plain text body", content_type="text/plain") as base_url:
            with JSONRunner(_env(base_url)) as r:
                outcome = r.trigger(Request(method="GET", path="/"))
        assert outcome.body is None
        assert outcome.raw == b"plain text body"

    def test_error_status_returned(self):
        with _serve(body=b'{"error": "not found"}', status=404) as base_url:
            with JSONRunner(_env(base_url)) as r:
                outcome = r.trigger(Request(method="GET", path="/missing"))
        assert outcome.status_code == 404
        assert outcome.body == {"error": "not found"}

    def test_duration_ms_recorded(self):
        with _serve() as base_url:
            with JSONRunner(_env(base_url)) as r:
                outcome = r.trigger(Request(method="GET", path="/"))
        assert outcome.duration_ms >= 0
