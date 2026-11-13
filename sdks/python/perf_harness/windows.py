"""Derive traffic ranges from serial lifecycle facts; never clip request durations."""

from dataclasses import replace

from perf_harness.model import PhaseRun, TimeRange, Window


def merge_phases(segments: list[PhaseRun]) -> list[PhaseRun]:
    """Collapse adjacent load steps into their lifecycle phase."""
    phases: list[PhaseRun] = []
    for segment in segments:
        if phases and phases[-1].kind == segment.kind:
            previous = phases[-1]
            previous.end_s = segment.end_s
            previous.complete &= segment.complete
            previous.limited_s += segment.limited_s
            previous.end_reason = segment.end_reason
            if previous.target_level != segment.target_level:
                previous.target_level = None
        else:
            phases.append(replace(segment, id=segment.kind, name=segment.kind))
    return phases


def traffic_windows(phases: list[PhaseRun], ranges: list[TimeRange]) -> list[Window]:
    """Setup/cleanup never contribute to throughput denominators or metric queries.

    @spec Phases describe execution; ranges select evidence without changing its timestamps.
    """
    traffic = [p for p in phases if p.kind in {"warmup", "hold", "cooldown"}]
    start = traffic[0].start_s if traffic else 0.0
    end = traffic[-1].end_s if traffic else start
    windows = [
        Window(
            "full",
            "Full observation",
            "full",
            start,
            end,
            bool(traffic) and all(p.complete for p in traffic),
            limited_s=sum(p.limited_s for p in traffic),
        )
    ]
    windows.extend(
        Window(
            p.id,
            p.name,
            "phase",
            p.start_s,
            p.end_s,
            p.complete,
            phase=p.kind,
            target_level=p.target_level,
            end_reason=p.end_reason,
            limited_s=p.limited_s,
        )
        for p in traffic
    )
    for index, selection in enumerate(ranges):
        left, right = max(start, selection.start_s), min(end, selection.end_s)
        if right <= left:
            continue
        windows.append(
            Window(
                f"custom-{index}",
                selection.name,
                "custom",
                left,
                right,
                start <= selection.start_s and end >= selection.end_s,
            )
        )
    return windows
