"""Metric protocol — ``BaseMetric[T]`` + ``MetricResult``.

The soft-scoring counterpart to deterministic assertions: e2e metrics read an
``Outcome`` (HTTP / SSE protocol response) and score it. ``BaseMetric`` stays
generic over the target type ``T`` so the abstraction isn't tied to ``Outcome``,
but in this package the only target is ``Outcome`` (dataset-sample scoring lives
in the sibling ``eval_harness`` package).

Subclasses declare ``NAME`` and (optionally) ``KIND`` as class attributes, then
implement ``score(target)``. Sync and async share the surface — ``score`` may
return a ``MetricResult`` or an awaitable of one; consumers await transparently.

``KIND`` controls report rendering:

- ``"binary"`` — score is 0.0 or 1.0. Report tables render ``yes`` / ``no``.
- ``"score"`` (default) — score is any value in 0~1. Tables render the decimal.

Two construction patterns coexist by design:

1. Class-level NAME (a fixed-identity metric)::

       class _StatusOkMetric(BaseMetric[Outcome]):
           NAME = "status_ok"
           KIND = "binary"
           def score(self, outcome):
               return self.result(1.0 if outcome.status_code == 200 else 0.0, "...")

2. Instance-level NAME (same class, different config per use site)::

       class LatencyMetric(BaseMetric[Outcome]):
           NAME = "latency"
           def __init__(self, *, target_ms, name=None):
               super().__init__(name=name)   # overrides NAME if provided
               self._target = target_ms

       fast = LatencyMetric(target_ms=2000, name="fast-lat")
       slow = LatencyMetric(target_ms=10000, name="slow-lat")

Use the ``self.result(score, judgement)`` helper inside ``score`` — it stamps
``name`` and ``kind`` automatically.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Awaitable, Generic, Literal, TypeVar

MetricKind = Literal["binary", "score"]


@dataclass(frozen=True, slots=True)
class MetricResult:
    name: str
    score: float | None  # 0~1; None when the metric doesn't apply to this target
    judgement: str  # short human-readable rationale
    kind: MetricKind = "score"


T = TypeVar("T")


class BaseMetric(Generic[T], ABC):
    """Score a single ``T`` into a ``MetricResult``. Generic over the target type."""

    NAME: str
    KIND: MetricKind = "score"

    def __init__(self, name: str | None = None) -> None:
        if name is not None:
            self.NAME = name
        if not getattr(self, "NAME", None):
            raise TypeError(
                f"{type(self).__name__} must set NAME (class attribute or __init__(name=...))"
            )

    def applies_to(self, target: T) -> bool:
        """Override to filter inputs this metric applies to. Default: all."""
        return True

    @abstractmethod
    def score(self, target: T) -> MetricResult | Awaitable[MetricResult]:
        """Compute the metric for ``target``. Sync or async."""

    def result(self, score: float | None, judgement: str) -> MetricResult:
        """Convenience builder: fills ``name`` and ``kind`` from this metric."""
        return MetricResult(
            name=self.NAME, score=score, judgement=judgement, kind=self.KIND
        )
