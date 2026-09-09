"""Context-aware polling and temporal assertions for long-running e2e cases."""

from __future__ import annotations

import time
from collections.abc import Callable

from e2e_harness.caserun import PhaseContext


def poll(ctx: PhaseContext, interval_s: float, check: Callable[[], bool]) -> None:
    """Return when ``check`` becomes true; raise when the phase deadline expires."""
    _require_interval(interval_s)
    while True:
        if check():
            return
        _sleep(ctx, interval_s)


def retry(
    ctx: PhaseContext,
    interval_s: float,
    operation: Callable[[], tuple[bool, Exception | None]],
) -> None:
    """Retry caller-classified transient errors until success or deadline."""
    _require_interval(interval_s)
    while True:
        retryable, error = operation()
        if error is None:
            return
        if not retryable:
            raise error
        try:
            _sleep(ctx, interval_s)
        except TimeoutError as timeout:
            raise TimeoutError(f"retry expired; last error: {error}") from timeout


def consistently(
    ctx: PhaseContext,
    duration_s: float,
    interval_s: float,
    check: Callable[[], bool],
) -> None:
    """Require ``check`` to stay true for an explicit observation window."""
    _require_interval(interval_s)
    if duration_s <= 0:
        raise ValueError("duration_s must be positive")
    if duration_s >= ctx.remaining_s:
        raise ValueError("duration_s must fit within the remaining phase budget")
    deadline = time.monotonic() + duration_s
    while True:
        if not check():
            raise AssertionError("condition became false")
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            return
        time.sleep(min(interval_s, remaining))


def _sleep(ctx: PhaseContext, interval_s: float) -> None:
    remaining = ctx.remaining_s
    if remaining <= 0:
        ctx.raise_if_expired()
    time.sleep(min(interval_s, remaining))
    ctx.raise_if_expired()


def _require_interval(interval_s: float) -> None:
    if interval_s <= 0:
        raise ValueError("interval_s must be positive")
