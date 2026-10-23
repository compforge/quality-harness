"""Harness-neutral result of one service Operation."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass
class Outcome:
    """Raw domain evidence produced by one OperationRun.

    Protocol fields intentionally remain on harness-specific subclasses: an E2E
    HTTP response and a perf request sample are both Outcomes, but do not share a
    useful wire shape beyond that semantic role.
    """
