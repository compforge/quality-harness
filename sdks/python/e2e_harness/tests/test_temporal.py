from __future__ import annotations

import time

import pytest

from e2e_harness.caserun import PhaseContext
from e2e_harness.temporal import consistently, poll, retry


def _ctx(duration_s: float = 0.05) -> PhaseContext:
    start = time.monotonic()
    return PhaseContext("judge", start, start + duration_s)


def test_poll_eventually_succeeds():
    attempts = 0

    def check() -> bool:
        nonlocal attempts
        attempts += 1
        return attempts == 3

    poll(_ctx(), 0.001, check)
    assert attempts == 3


def test_retry_stops_on_non_retryable_error():
    error = ValueError("invalid request")
    with pytest.raises(ValueError, match="invalid request"):
        retry(_ctx(), 0.001, lambda: (False, error))


def test_consistently_fails_when_condition_changes():
    attempts = 0

    def check() -> bool:
        nonlocal attempts
        attempts += 1
        return attempts < 3

    with pytest.raises(AssertionError, match="became false"):
        consistently(_ctx(), 0.02, 0.001, check)


def test_consistently_finishes_before_the_phase_deadline():
    ctx = _ctx(0.05)
    consistently(ctx, 0.005, 0.001, lambda: True)
    ctx.raise_if_expired()


def test_consistently_rejects_a_window_that_consumes_the_phase_budget():
    with pytest.raises(ValueError, match="fit within"):
        consistently(_ctx(0.01), 0.02, 0.001, lambda: True)
