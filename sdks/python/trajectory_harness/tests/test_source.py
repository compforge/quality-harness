from __future__ import annotations

from datetime import UTC, datetime

import pytest

from trajectory_harness import (
    OTelJsonLoader,
    Recording,
    RecordingQuery,
    RecordingRef,
    RecordingSource,
)
from trajectory_harness.model import trajectory_source


class MemorySource:
    def __init__(self, recording: Recording) -> None:
        self.recording = recording

    def select(self, query: RecordingQuery | None = None) -> list[RecordingRef]:
        query = query or RecordingQuery()
        ref = self.recording.ref
        if query.started_at_or_after and ref.started_at < query.started_at_or_after:
            return []
        if query.started_before and ref.started_at >= query.started_before:
            return []
        if any(
            ref.attributes.get(key) != value for key, value in query.attributes.items()
        ):
            return []
        return [ref]

    def fetch(self, ref: RecordingRef) -> Recording:
        assert ref == self.recording.ref
        return self.recording


def test_source_composes_with_in_memory_loader() -> None:
    ref = RecordingRef(
        recording_id="recording-1",
        uri="memory://recording-1",
        started_at=datetime(2026, 8, 22, tzinfo=UTC),
        attributes={"repository": "example/repo"},
    )
    recording = Recording(
        ref=ref,
        text=(
            '{"traceId":"trace-1","spanId":"span-1",'
            '"attributes":{"gen_ai.operation.name":"chat"}}'
        ),
    )
    source = MemorySource(recording)

    assert isinstance(source, RecordingSource)
    selected = source.select(RecordingQuery(attributes={"repository": "example/repo"}))
    trajectories = OTelJsonLoader().loads(
        source.fetch(selected[0]).text,
        source=selected[0].uri,
    )

    assert trajectories[0].trajectory_id == "trace-1"
    assert trajectory_source(trajectories[0]) == "memory://recording-1"


def test_recording_query_rejects_non_positive_limit() -> None:
    with pytest.raises(ValueError, match="positive"):
        RecordingQuery(limit=0)
