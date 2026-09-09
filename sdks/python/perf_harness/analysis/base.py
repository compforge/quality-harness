"""Observation — one pre-chewed, machine-backed finding over a Run.

The analysis package does the *mechanical* 80% of what a perf analyst does:
scaling ratios, Little's law residuals, headroom vs request/limit, slope
extrapolation, sample adequacy, breaker reachability. Each lens emits
``Observation``s: a one-line human title plus the exact numbers behind it
(``evidence``), tagged ``fact`` (computed truth) or ``flag`` (limits the
conclusions / needs attention). The judgement 20% — what it *means*, what to do —
stays with the reader (human or agent); observations just save them the legwork.

Reads go through ``MetricStore`` (the same face report/SLO use), so an analyzer
can never compute a number the rest of the system couldn't address.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal

from perf_harness.model import TrialRecord


@dataclass(frozen=True)
class Observation:
    analyzer: str  # which lens produced it: capacity / resource / latency / validity
    kind: Literal["fact", "flag"]  # flag = caveat or attention point, fact = computed truth
    title: str  # one human-readable line
    evidence: dict[str, object] = field(default_factory=dict)  # the numbers backing the title


def by_resources(trials: list[TrialRecord]) -> list[tuple[str, list[TrialRecord]]]:
    """Group trials by resource-profile label, each group sorted by peak level — the
    'sweep curves' every lens walks (x = load level within one profile).

    A phase error invalidates a curve point only when measurement is incomplete.
    Errors from later lifecycle work still fail the run and stop the sweep, but
    they do not erase a complete measurement that was already observed.
    """
    groups: dict[str, list[TrialRecord]] = {}
    for r in trials:
        if r.phase_errors and not r.measurement.complete:
            continue
        groups.setdefault(r.arm.resources.label(), []).append(r)
    return [
        (label, sorted(rs, key=lambda r: r.arm.load.schedule.peak_level))
        for label, rs in groups.items()
    ]


def linfit(points: list[tuple[float, float]]) -> tuple[float, float] | None:
    """Least-squares line over ``(x, y)`` points → ``(slope, intercept)``; ``None``
    when underdetermined (<2 distinct x). Used for usage-vs-level slopes."""
    if len({x for x, _ in points}) < 2:
        return None
    n = len(points)
    sx = sum(x for x, _ in points)
    sy = sum(y for _, y in points)
    sxx = sum(x * x for x, _ in points)
    sxy = sum(x * y for x, y in points)
    denom = n * sxx - sx * sx
    if denom == 0:
        return None
    slope = (n * sxy - sx * sy) / denom
    return slope, (sy - slope * sx) / n


def pct(part: float, whole: float) -> float | None:
    """``part`` as a % of ``whole`` (1 decimal); None when the bound is absent/zero."""
    return round(part / whole * 100, 1) if whole else None
