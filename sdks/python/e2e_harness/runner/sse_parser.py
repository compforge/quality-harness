"""SSE wire-format parser as a reusable state machine.

The same parser drives both ``SSERunner`` (sync) and async streaming callers
(SSE consumers, future ``AsyncSSERunner``). Push lines one at a time;
the parser emits ``SSEEvent`` as each event completes (terminated by a blank
line). Call ``flush()`` at stream end to drain a trailing event that was not
followed by a blank line.

EventSource spec / RFC compliance:
- ``event: <name>`` (optional, default ``"message"``)
- ``id: <id>`` (optional)
- One or more ``data: <chunk>`` lines per event
- Blank line terminates an event
- Lines starting with ``:`` are comments / heartbeats; ignored
- Unknown fields (``retry``, etc.) are ignored
"""

from __future__ import annotations

import json
from typing import Any


class SSEEvent:
    """One parsed SSE event."""

    __slots__ = ("event", "data", "id")

    def __init__(self, *, event: str = "message", data: str = "", id: str = ""):
        self.event = event
        self.data = data
        self.id = id

    def json(self) -> dict[str, Any] | None:
        """Parse ``data`` as JSON dict; None if not JSON or not an object."""
        if not self.data:
            return None
        try:
            parsed = json.loads(self.data)
        except (ValueError, json.JSONDecodeError):
            return None
        return parsed if isinstance(parsed, dict) else None

    def to_dict(self) -> dict[str, Any]:
        """Serializable dict form. Stored in ``Outcome.metadata['events']``."""
        out: dict[str, Any] = {"event": self.event}
        parsed = self.json()
        out["data"] = parsed if parsed is not None else self.data
        if self.id:
            out["id"] = self.id
        return out


class SSEParser:
    """Stateful SSE parser. Sync and async callers use the same instance."""

    def __init__(self) -> None:
        self._event = "message"
        self._data_lines: list[str] = []
        self._id = ""

    def feed_line(self, line: str) -> SSEEvent | None:
        """Process one line. Returns an event if the line completes one, else None.

        Strips trailing whitespace (incl. CRLF) so callers can feed raw lines
        from ``iter_lines`` / ``aiter_lines`` directly.
        """
        line = line.rstrip()
        if not line:
            if self._data_lines:
                return self._emit()
            return None
        if line.startswith(":"):
            return None  # comment / heartbeat
        if ":" in line:
            field, _, value = line.partition(":")
            if value.startswith(" "):
                value = value[1:]
        else:
            field, value = line, ""
        if field == "event":
            self._event = value
        elif field == "data":
            self._data_lines.append(value)
        elif field == "id":
            self._id = value
        # other fields (retry) are ignored per spec
        return None

    def flush(self) -> SSEEvent | None:
        """Emit a trailing event that wasn't followed by a blank line. Idempotent."""
        if self._data_lines:
            return self._emit()
        return None

    def _emit(self) -> SSEEvent:
        evt = SSEEvent(event=self._event, data="\n".join(self._data_lines), id=self._id)
        self._event = "message"
        self._data_lines = []
        self._id = ""
        return evt
