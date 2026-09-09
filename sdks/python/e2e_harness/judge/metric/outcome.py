"""Outcome metrics — ``BaseMetric[Outcome]`` subclasses for e2e runs.

When an API/SSE case wants soft scores
(latency, status, event count, ...) alongside hard assertions, attach
``BaseMetric[Outcome]`` instances and call ``score_outcome(outcome, metrics)``.

These metrics are sync; LLM-judge async lives in
``e2e_harness.judge.metric.llm_judge`` for the dataset-mode flow.

Usage::

    def test_chat_smoke(outcome):
        scores = score_outcome(outcome, [
            LatencyMetric(target_ms=2000, cutoff_ms=10000),
            StatusMetric(expected=200),
        ])
        for r in scores:
            assert r.score is not None and r.score >= 0.7, r.judgement

Same class can be instantiated with different ``name=`` values when more than
one configuration is in play (``LatencyMetric(target_ms=2000, name="fast")``
and ``LatencyMetric(target_ms=10000, name="slow")``).
"""

from __future__ import annotations

from typing import Sequence

from e2e_harness.judge.metric.base import BaseMetric, MetricResult
from e2e_harness.runner.base import Outcome


def score_outcome(
    outcome: Outcome,
    metrics: Sequence[BaseMetric[Outcome]],
) -> list[MetricResult]:
    """Run every metric over the outcome; collect results in order.

    Sync only. If a metric here returns an awaitable, raises ``TypeError`` so
    misuse fails loudly (async metrics belong in the dataset eval engine).
    """
    results: list[MetricResult] = []
    for m in metrics:
        r = m.score(outcome)
        if not isinstance(r, MetricResult):
            raise TypeError(
                f"score_outcome requires sync metrics; {m.NAME!r} returned "
                f"{type(r).__name__}. Use the dataset eval engine for async metrics."
            )
        results.append(r)
    return results


# --------------------------------------------------------------------------- built-ins


class LatencyMetric(BaseMetric[Outcome]):
    """1.0 if ``duration_ms <= target_ms``, linearly decaying to 0.0 at ``cutoff_ms``.

    ``cutoff_ms`` defaults to ``target_ms * 5`` if not provided.
    """

    NAME = "latency"

    def __init__(
        self,
        *,
        target_ms: float,
        cutoff_ms: float | None = None,
        name: str | None = None,
    ):
        super().__init__(name=name)
        if target_ms <= 0:
            raise ValueError("target_ms must be positive")
        self._target = float(target_ms)
        self._cutoff = (
            float(cutoff_ms) if cutoff_ms is not None else float(target_ms) * 5
        )
        if self._cutoff <= self._target:
            raise ValueError("cutoff_ms must be greater than target_ms")

    def score(self, outcome: Outcome) -> MetricResult:
        d = float(outcome.duration_ms)
        if d <= self._target:
            return self.result(
                1.0, f"duration={d:.0f}ms <= target={self._target:.0f}ms"
            )
        if d >= self._cutoff:
            return self.result(
                0.0, f"duration={d:.0f}ms >= cutoff={self._cutoff:.0f}ms"
            )
        span = self._cutoff - self._target
        s = round(1.0 - (d - self._target) / span, 3)
        return self.result(
            s,
            f"duration={d:.0f}ms (target={self._target:.0f}, cutoff={self._cutoff:.0f})",
        )


class StatusMetric(BaseMetric[Outcome]):
    """Binary: 1.0 if ``status_code`` matches ``expected``, else 0.0.

    Pass an int (exact) or a tuple (range like ``(200, 300)`` for 2xx).
    """

    NAME = "status"
    KIND = "binary"

    def __init__(
        self, *, expected: int | tuple[int, int] = 200, name: str | None = None
    ):
        super().__init__(name=name)
        self._expected = expected

    def score(self, outcome: Outcome) -> MetricResult:
        actual = outcome.status_code
        if isinstance(self._expected, tuple):
            lo, hi = self._expected
            ok = lo <= actual < hi
            desc = f"[{lo}, {hi})"
        else:
            ok = actual == self._expected
            desc = str(self._expected)
        return self.result(1.0 if ok else 0.0, f"got {actual}, expected {desc}")


class EventCountMetric(BaseMetric[Outcome]):
    """Binary: 1.0 if ``outcome.metadata['event_count'] >= min_count``.

    Useful for asserting "the stream produced at least N chunks" without
    enforcing exact count.
    """

    NAME = "event_count"
    KIND = "binary"

    def __init__(self, *, min_count: int = 1, name: str | None = None):
        super().__init__(name=name)
        if min_count < 0:
            raise ValueError("min_count must be non-negative")
        self._min = min_count

    def score(self, outcome: Outcome) -> MetricResult:
        count = outcome.metadata.get("event_count", 0)
        if not isinstance(count, int):
            return self.result(
                None,
                f"metadata.event_count missing or non-int (got {type(count).__name__})",
            )
        ok = count >= self._min
        return self.result(1.0 if ok else 0.0, f"events={count}, min={self._min}")
