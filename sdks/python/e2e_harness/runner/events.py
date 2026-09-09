"""Event accessors over an SSE response — public, runner-agnostic helpers.

Both the sync ``AgentCase`` (pytest-driven) and the async eval pipeline use
these to read events out of ``Outcome.metadata['events']`` (produced by
``SSERunner``) or a raw events list (produced by an async streaming caller).

Function inputs accept either:
- ``Outcome``: events are pulled from ``outcome.metadata['events']``
- ``list[dict]``: a raw events list (e.g. async streaming caller already
  collected events without constructing an Outcome)

This lets the same helpers serve both pyramids: pytest test cases that
``case.sse_action_post(...)`` and async eval runners that collect events
out-of-band.
"""

from __future__ import annotations

from typing import Any, Sequence

from e2e_harness.runner.base import Outcome

EventLike = Outcome | Sequence[dict[str, Any]]


def events(source: EventLike) -> list[dict[str, Any]]:
    """Return the events list from either an Outcome or a raw list."""
    if isinstance(source, Outcome):
        raw = source.metadata.get("events")
        return list(raw) if isinstance(raw, list) else []
    if isinstance(source, (list, tuple)):
        return list(source)
    raise TypeError(
        f"expected Outcome or list of event dicts, got {type(source).__name__}"
    )


def find_event(
    source: EventLike,
    *,
    op: str | None = None,
    event: str | None = None,
) -> dict[str, Any] | None:
    """First event matching ``op`` (data.op) and/or ``event`` (SSE event name).

    Pass either or both. Returns the raw event dict ({event, data, [id]}) or
    None if not found.
    """
    for evt in events(source):
        if event is not None and evt.get("event") != event:
            continue
        if op is not None:
            data = evt.get("data")
            if not isinstance(data, dict) or data.get("op") != op:
                continue
        return evt
    return None


def find_event_data(
    source: EventLike,
    *,
    op: str | None = None,
    event: str | None = None,
) -> dict[str, Any] | None:
    """Like ``find_event`` but unwraps the ``data`` dict directly."""
    evt = find_event(source, op=op, event=event)
    if evt is None:
        return None
    data = evt.get("data")
    return data if isinstance(data, dict) else None


def collect_text(
    source: EventLike,
    *,
    op: str = "text_delta",
    key: str = "delta",
) -> str:
    """Concatenate string ``data[key]`` across all events with matching ``op``.

    Typical chat pattern: many ``{"op": "text_delta", "delta": "..."}`` events.
    """
    parts: list[str] = []
    for evt in events(source):
        data = evt.get("data")
        if not isinstance(data, dict) or data.get("op") != op:
            continue
        v = data.get(key)
        if isinstance(v, str):
            parts.append(v)
    return "".join(parts)


def count_events(source: EventLike, *, op: str | None = None) -> int:
    """Total event count; if ``op`` given, only events with matching ``data.op``."""
    if op is None:
        return len(events(source))
    return sum(
        1
        for evt in events(source)
        if isinstance(evt.get("data"), dict) and evt["data"].get("op") == op
    )
