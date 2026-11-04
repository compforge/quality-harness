"""Native ATIF v1.8 JSON loader."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from atif import Trajectory

from trajectory_harness.model import (
    trajectory_from_dict,
    trajectory_recording_id,
    with_recording,
)


class ATIFJsonLoader:
    """Load standard ATIF trajectory objects from JSON, arrays, or JSONL."""

    def load(self, source: str | Path) -> list[Trajectory]:
        path = Path(source)
        return self.loads(path.read_text(encoding="utf-8"), source=str(path))

    def loads(self, text: str, *, source: str = "") -> list[Trajectory]:
        text = text.strip()
        if not text:
            return []
        try:
            value = json.loads(text)
        except json.JSONDecodeError:
            value = [json.loads(line) for line in text.splitlines() if line.strip()]
        values = value if isinstance(value, list) else [value]
        trajectories = [trajectory_from_dict(_object(item)) for item in values]
        if source:
            trajectories = [
                with_recording(
                    item, recording_id=trajectory_recording_id(item), source=source
                )
                for item in trajectories
            ]
        return trajectories


def _object(value: Any) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ValueError("ATIF JSON must contain an object or a sequence of objects")
    return value
