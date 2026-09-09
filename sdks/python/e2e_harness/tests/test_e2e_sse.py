"""SSE coverage for the engine: a streaming SUT (over httpx MockTransport) → SSERunner →
judgment over the event stream as data (events[].event / event_count).

Same engine, same case shape as the JSON path — only the runner differs. Proves 判定即数据
extends to agent / streaming endpoints without per-case code.
"""

from __future__ import annotations

from pathlib import Path

import httpx

from spec_case.model import load_caseset, validate
from e2e_harness.cli import _runner as cli_runner
from e2e_harness.core.config import E2EConfig
from e2e_harness.engine import response_view, run_cases
from e2e_harness.runner.base import Outcome
from e2e_harness.runner.sse_runner import SSERunner

_CASES = Path(__file__).parent.parent / "examples" / "chat_cases.yaml"

# a streamed SSE chat response: two content frames + a terminal `done` frame
_STREAM = (
    b'event: message\ndata: {"token": "hi"}\n\n'
    b'event: message\ndata: {"token": " there"}\n\n'
    b"event: done\ndata: [DONE]\n\n"
)


def _sse_sut(request: httpx.Request) -> httpx.Response:
    if request.url.path == "/chat":
        return httpx.Response(
            200, content=_STREAM, headers={"content-type": "text/event-stream"}
        )
    return httpx.Response(404)


def _runner() -> SSERunner:
    return SSERunner(
        E2EConfig(),
        client=httpx.Client(
            transport=httpx.MockTransport(_sse_sut), base_url="http://sut"
        ),
    )


def test_cli_protocol_sse_selects_sse_runner():
    assert isinstance(
        cli_runner(E2EConfig(), "sse"), SSERunner
    )  # `e2e run --protocol sse`


def test_response_view_exposes_events_and_count():
    outcome = Outcome(
        status_code=200, metadata={"events": [{"event": "done"}], "event_count": 1}
    )
    view = response_view(outcome)
    assert view["events"] == [{"event": "done"}] and view["event_count"] == 1


def test_chat_caseset_passes_over_the_event_stream():
    cs = load_caseset(_CASES)
    validate(cs)
    rv = run_cases(cs.cases, _runner(), scope="chat", run_id="t1")
    assert rv.status == "pass"  # status 200, a `done` frame present, event_count > 1
    assert rv.summary["pass"] == 1


def test_engine_flags_a_truncated_stream():
    # a contract that the SUT violates: require a 'done' frame the truncated stream never sends
    def _truncated(request: httpx.Request) -> httpx.Response:
        body = b'event: message\ndata: {"token": "hi"}\n\n'  # no terminal done frame
        return httpx.Response(
            200, content=body, headers={"content-type": "text/event-stream"}
        )

    runner = SSERunner(
        E2EConfig(),
        client=httpx.Client(
            transport=httpx.MockTransport(_truncated), base_url="http://sut"
        ),
    )
    cs = load_caseset(_CASES)
    rv = run_cases(cs.cases, runner, scope="chat", run_id="t2")
    assert rv.status == "fail" and "done" in (rv.cases[0].reason or "")
