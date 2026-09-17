"""Comparable sweep slices shared by analysis and reports.

The scan axis is request_rate for finite arrivals, max_concurrency for saturated
loads. Everything else stays fixed, including the other axis's complete schedule.
This is a projection of existing Arm facts, not an additional experiment model.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import asdict

from perf_harness.model import ArmRun


def scan_axis(r: ArmRun) -> str:
    return "max_concurrency" if r.arm.load.saturated else "request_rate"


def axis_label(r: ArmRun) -> str:
    return "max_concurrency (requests)" if r.arm.load.saturated else "request_rate (requests/s)"


def comparison_conditions(r: ArmRun) -> dict:
    """Fixed conditions plus the shape of the scanned axis, normalized by its peak.

    Equal peaks do not imply equal pressure histories. Only proportional scan-axis
    schedules can share a curve; the other axis is never normalized away.
    """
    load = r.arm.load
    axis = scan_axis(r)
    scale = load.peak_level or 1.0
    conditions = asdict(load)
    conditions[axis] /= scale
    conditions["stages"] = [
        {
            "kind": stage.kind,
            "duration_s": stage.duration_s,
            "request_rate": "inf" if load.saturated else stage.request_rate,
            "max_concurrency": stage.max_concurrency,
        }
        for stage in load.planned_stages
    ]
    for stage in conditions["stages"]:
        stage[axis] /= scale
    if load.saturated:
        conditions["request_rate"] = "inf"
    return {"scan_axis": axis, "resources": asdict(r.arm.resources), "load": conditions}


def comparison_groups(
    arm_runs: list[ArmRun], *, include_partial: bool = False
) -> list[tuple[str, list[ArmRun]]]:
    """Partition by full comparison conditions, then order each slice by scan level.

    A phase error only invalidates a point when measurement is incomplete. A later
    cleanup failure cannot erase an already complete measurement. Window-level
    capacity uses include_partial to retain earlier complete holds after a later failure.
    """
    groups: dict[str, list[ArmRun]] = {}
    for r in arm_runs:
        if not include_partial and r.phase_errors and not r.measurement.complete:
            continue
        key = json.dumps(comparison_conditions(r), sort_keys=True, separators=(",", ":"))
        groups.setdefault(key, []).append(r)
    result = []
    for key, rows in groups.items():
        r = rows[0]
        fixed = (
            "request_rate=inf"
            if r.arm.load.saturated
            else f"max_concurrency={r.arm.load.peak_concurrency}"
        )
        # The full key determines membership; the short hash is display-only.
        label = (
            f"{r.arm.resources.label()}|{axis_label(r)}|{fixed}|"
            f"conditions={hashlib.sha256(key.encode()).hexdigest()[:8]}"
        )
        result.append((label, sorted(rows, key=lambda r: r.arm.load.peak_level)))
    return result
