"""Tests for the public event accessor helpers in e2e_harness.runner.events."""

from __future__ import annotations

import pytest

from e2e_harness.runner.base import Outcome
from e2e_harness.runner.events import (
    collect_text,
    count_events,
    events,
    find_event,
    find_event_data,
)


def _events_list() -> list[dict]:
    return [
        {"event": "message", "data": {"op": "start", "id": "x"}},
        {"event": "message", "data": {"op": "text_delta", "delta": "Hel"}},
        {"event": "message", "data": {"op": "text_delta", "delta": "lo"}},
        {"event": "done", "data": {"op": "done"}},
    ]


def _outcome(events_list: list[dict]) -> Outcome:
    return Outcome(
        status_code=200,
        body=None,
        metadata={"events": events_list, "event_count": len(events_list)},
    )


class TestAcceptsBothInputs:
    """Each helper takes either an Outcome or a raw events list."""

    def test_events_from_outcome(self):
        assert events(_outcome(_events_list())) == _events_list()

    def test_events_from_list(self):
        assert events(_events_list()) == _events_list()

    def test_events_outcome_missing_key(self):
        oc = Outcome(status_code=200, metadata={})
        assert events(oc) == []

    def test_events_rejects_unknown_type(self):
        with pytest.raises(TypeError, match="Outcome"):
            events("not an event collection")  # type: ignore[arg-type]


class TestFindEvent:
    def test_by_op_via_outcome(self):
        evt = find_event(_outcome(_events_list()), op="start")
        assert evt is not None
        assert evt["data"]["id"] == "x"

    def test_by_op_via_list(self):
        evt = find_event(_events_list(), op="text_delta")
        assert evt is not None and evt["data"]["delta"] == "Hel"

    def test_by_event_name(self):
        assert find_event(_events_list(), event="done")["data"]["op"] == "done"

    def test_combined_op_and_event(self):
        # done op + done event
        e = find_event(_events_list(), op="done", event="done")
        assert e is not None
        # done op + message event — should NOT match (event mismatches)
        assert find_event(_events_list(), op="done", event="message") is None

    def test_missing_returns_none(self):
        assert find_event(_events_list(), op="not_a_real_op") is None


class TestFindEventData:
    def test_unwraps_data(self):
        data = find_event_data(_events_list(), op="start")
        assert data == {"op": "start", "id": "x"}

    def test_none_when_data_not_dict(self):
        raw = [{"event": "m", "data": "raw string"}]
        assert find_event_data(raw) is None


class TestCollectText:
    def test_concatenates_default_op(self):
        assert collect_text(_events_list()) == "Hello"

    def test_custom_op_and_key(self):
        raw = [
            {"event": "m", "data": {"op": "chunk", "content": "ab"}},
            {"event": "m", "data": {"op": "chunk", "content": "cd"}},
        ]
        assert collect_text(raw, op="chunk", key="content") == "abcd"

    def test_outcome_input(self):
        oc = _outcome(_events_list())
        assert collect_text(oc) == "Hello"


class TestCountEvents:
    def test_total(self):
        assert count_events(_events_list()) == 4

    def test_by_op(self):
        assert count_events(_events_list(), op="text_delta") == 2

    def test_outcome_input(self):
        assert count_events(_outcome(_events_list()), op="start") == 1
