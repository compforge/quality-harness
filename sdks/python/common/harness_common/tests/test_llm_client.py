"""Tests for LLMClient + LLMConfig + retry classification."""

from __future__ import annotations

import asyncio
import json
import threading
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, HTTPServer
from typing import Iterator

import httpx

from harness_common.llm import ChatResult, LLMClient, LLMConfig
from harness_common.llm.client import _is_retryable


# --------------------------------------------------------------------------- LLMConfig


class TestLLMConfig:
    def test_from_env_defaults(self, monkeypatch):
        monkeypatch.delenv("EVAL_JUDGE_BASE", raising=False)
        monkeypatch.delenv("EVAL_JUDGE_KEY", raising=False)
        monkeypatch.delenv("EVAL_JUDGE_MODEL", raising=False)
        cfg = LLMConfig.from_env()
        assert not cfg.ready()
        assert cfg.base_url == ""

    def test_from_env_resolves(self, monkeypatch):
        monkeypatch.setenv("EVAL_JUDGE_BASE", "https://example.com/v1/")
        monkeypatch.setenv("EVAL_JUDGE_KEY", "k")
        monkeypatch.setenv("EVAL_JUDGE_MODEL", "gpt-4o")
        cfg = LLMConfig.from_env()
        assert cfg.base_url == "https://example.com/v1"  # trailing / stripped
        assert cfg.ready()

    def test_custom_env_var_names(self, monkeypatch):
        monkeypatch.setenv("MY_BASE", "http://x")
        monkeypatch.setenv("MY_KEY", "k")
        monkeypatch.setenv("MY_MODEL", "m")
        cfg = LLMConfig.from_env(
            base_env="MY_BASE", key_env="MY_KEY", model_env="MY_MODEL"
        )
        assert cfg.ready()

    def test_client_from_env(self, monkeypatch):
        monkeypatch.setenv("EVAL_JUDGE_BASE", "http://x")
        monkeypatch.setenv("EVAL_JUDGE_KEY", "k")
        monkeypatch.setenv("EVAL_JUDGE_MODEL", "m")
        assert LLMClient.from_env().ready()


# --------------------------------------------------------------------------- retry classification


def _req() -> httpx.Request:
    return httpx.Request("POST", "http://x/chat/completions")


class TestIsRetryable:
    def test_timeout_and_transport_retryable(self):
        assert _is_retryable(httpx.TimeoutException("t"))
        assert _is_retryable(httpx.ConnectError("c"))

    def test_5xx_and_429_retryable(self):
        for code in (429, 500, 503):
            exc = httpx.HTTPStatusError(
                "e", request=_req(), response=httpx.Response(code, request=_req())
            )
            assert _is_retryable(exc), code

    def test_4xx_not_retryable(self):
        for code in (400, 401, 404):
            exc = httpx.HTTPStatusError(
                "e", request=_req(), response=httpx.Response(code, request=_req())
            )
            assert not _is_retryable(exc), code

    def test_other_exceptions_not_retryable(self):
        assert not _is_retryable(ValueError("x"))


# --------------------------------------------------------------------------- HTTP-backed chat


class _Handler(BaseHTTPRequestHandler):
    """Mock /chat/completions: fail first ``fail_times`` calls, then return 200."""

    fail_times = 0
    fail_status = 503
    calls = 0
    content = "hello"
    model = "served-model"
    captured_payload: dict | None = None

    def do_POST(self):  # noqa: N802
        cl = int(self.headers.get("Content-Length", 0))
        raw = self.rfile.read(cl) if cl else b"{}"
        _Handler.captured_payload = json.loads(raw)
        _Handler.calls += 1
        if _Handler.calls <= _Handler.fail_times:
            self.send_response(_Handler.fail_status)
            self.end_headers()
            self.wfile.write(b"transient")
            return
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        body = json.dumps(
            {
                "model": _Handler.model,
                "choices": [{"message": {"content": _Handler.content}}],
            }
        ).encode("utf-8")
        self.wfile.write(body)

    def log_message(self, *_):  # noqa
        pass


@contextmanager
def _serve(
    *, fail_times: int = 0, fail_status: int = 503, content: str = "hello"
) -> Iterator[str]:
    _Handler.fail_times = fail_times
    _Handler.fail_status = fail_status
    _Handler.content = content
    _Handler.calls = 0
    _Handler.captured_payload = None
    server = HTTPServer(("127.0.0.1", 0), _Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_port}"
    finally:
        server.shutdown()
        thread.join(timeout=2)


def _client(base_url: str, **cfg) -> LLMClient:
    return LLMClient(
        LLMConfig(base_url=base_url, api_key="k", model="m", retry_backoff_s=0, **cfg)
    )


class TestChat:
    def test_complete_returns_text_and_model(self):
        with _serve(content="the answer") as base_url:
            r = asyncio.run(_client(base_url).complete("sys", "usr"))
        assert isinstance(r, ChatResult)
        assert r.text == "the answer"
        assert r.model == "served-model"  # provenance from response
        # request shape
        p = _Handler.captured_payload
        assert p["model"] == "m"
        assert p["messages"] == [
            {"role": "system", "content": "sys"},
            {"role": "user", "content": "usr"},
        ]

    def test_retries_then_succeeds(self):
        with _serve(fail_times=2, content="ok") as base_url:
            r = asyncio.run(_client(base_url, max_retries=2).complete("s", "u"))
        assert r.text == "ok"
        assert _Handler.calls == 3  # 2 failures + 1 success

    def test_exhausts_retries_raises(self):
        with _serve(fail_times=10) as base_url:
            import pytest

            with pytest.raises(httpx.HTTPStatusError):
                asyncio.run(_client(base_url, max_retries=2).complete("s", "u"))
        assert _Handler.calls == 3  # max_retries + 1

    def test_4xx_not_retried(self):
        with _serve(fail_times=10, fail_status=400) as base_url:
            import pytest

            with pytest.raises(httpx.HTTPStatusError):
                asyncio.run(_client(base_url, max_retries=2).complete("s", "u"))
        assert _Handler.calls == 1  # 400 not retryable → single attempt
