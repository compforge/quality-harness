"""Tests for the reusable SSEParser state machine."""

from __future__ import annotations

from e2e_harness.runner.sse_parser import SSEEvent, SSEParser


def _events(parser: SSEParser, lines: list[str]) -> list[SSEEvent]:
    out: list[SSEEvent] = []
    for line in lines:
        evt = parser.feed_line(line)
        if evt is not None:
            out.append(evt)
    final = parser.flush()
    if final is not None:
        out.append(final)
    return out


class TestSSEParser:
    def test_single_event_default_name(self):
        events = _events(SSEParser(), ['data: {"k":1}', ""])
        assert len(events) == 1
        assert events[0].event == "message"
        assert events[0].json() == {"k": 1}

    def test_named_event(self):
        events = _events(SSEParser(), ["event: start", 'data: {"msg":"hi"}', ""])
        assert events[0].event == "start"
        assert events[0].json() == {"msg": "hi"}

    def test_event_with_id(self):
        events = _events(SSEParser(), ["event: x", "id: evt-1", "data: payload", ""])
        assert events[0].id == "evt-1"
        assert events[0].data == "payload"

    def test_multiline_data(self):
        events = _events(SSEParser(), ["data: line1", "data: line2", ""])
        assert events[0].data == "line1\nline2"

    def test_comment_lines_ignored(self):
        events = _events(SSEParser(), [": heartbeat", "data: hello", ""])
        assert len(events) == 1
        assert events[0].data == "hello"

    def test_blank_field_without_value(self):
        events = _events(SSEParser(), ["data", ""])
        assert len(events) == 1
        assert events[0].data == ""

    def test_multiple_events_state_resets(self):
        events = _events(
            SSEParser(),
            ["event: a", "data: 1", "", "event: b", "data: 2", "", "data: 3", ""],
        )
        assert [(e.event, e.data) for e in events] == [
            ("a", "1"),
            ("b", "2"),
            ("message", "3"),
        ]

    def test_trailing_event_via_flush(self):
        events = _events(SSEParser(), ["data: trailing"])
        assert len(events) == 1
        assert events[0].data == "trailing"

    def test_flush_idempotent(self):
        parser = SSEParser()
        parser.feed_line("data: x")
        assert parser.flush() is not None
        # second flush should be None — state was reset by _emit
        assert parser.flush() is None

    def test_unknown_field_ignored(self):
        events = _events(SSEParser(), ["retry: 5000", "data: payload", ""])
        assert events[0].data == "payload"

    def test_crlf_line_endings(self):
        events = _events(SSEParser(), ["data: hi\r", "\r"])
        assert events[0].data == "hi"


class TestSSEEvent:
    def test_to_dict_with_id(self):
        e = SSEEvent(event="ev", data="x", id="i-1")
        assert e.to_dict() == {"event": "ev", "data": "x", "id": "i-1"}

    def test_to_dict_omits_empty_id(self):
        e = SSEEvent(event="ev", data="x")
        assert "id" not in e.to_dict()

    def test_to_dict_parses_json_data(self):
        e = SSEEvent(data='{"a": 1}')
        assert e.to_dict()["data"] == {"a": 1}

    def test_to_dict_keeps_non_json_data(self):
        e = SSEEvent(data="not json")
        assert e.to_dict()["data"] == "not json"

    def test_json_returns_none_for_non_object(self):
        # JSON array is valid JSON but not a dict
        assert SSEEvent(data="[1,2,3]").json() is None
