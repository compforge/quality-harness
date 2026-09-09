"""Pinned chart colors — report-side display policy, never model metadata.

The limits reference lines must read the same on EVERY chart: limit = red (the
hard ceiling), request = orange (the soft line). Everything else rotates the
chart palette.
"""

from __future__ import annotations

_PINNED_COLORS = {
    "limits.cpu_limit": "#d62728",
    "limits.mem_limit": "#d62728",
    "limits.cpu_request": "#ff7f0e",
    "limits.mem_request": "#ff7f0e",
}


def family_color(family: str) -> str:
    """The family's pinned chart color ('' → palette rotation)."""
    return _PINNED_COLORS.get(family, "")
