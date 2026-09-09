"""``BaseMetric`` — read one Sample, produce one MetricResult.

Authors subclass and set ``NAME`` (+ optionally ``KIND`` / ``WEIGHT``), then
implement ``score``. ``score`` may be sync or async (LLM judges are async);
the scorer awaits when needed.

``WEIGHT`` is the metric's **default relative** weight for the quality
composite (``aggregate.weighted_overall``). It is relative — absolute value is
meaningless because the composite renormalises over the metrics actually
applicable to a sample, so a metric's weight stays stable across different
metric combinations. An experiment may override it per run.

``applies_to`` lets a quality metric abstain (e.g. correctness on a refusal
case). When it abstains, return ``self.na(...)`` (score=None) — the composite
drops it rather than scoring 0.

Set ``DIAGNOSTIC = True`` for a quality metric that should be reported but kept
OUT of the weighted composite (faithfulness / coverage / retrieval-recall). It
resolves to weight 0 by default (still overridable per experiment), so adding a
diagnostic metric never silently shifts the headline score — clearer than a bare
``WEIGHT = 0``, which reads like a mistake.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Awaitable

from eval_harness.model.sample import MetricKind, MetricResult, Sample


class BaseMetric(ABC):
    NAME: str
    KIND: MetricKind = "score"
    WEIGHT: float = 1.0
    DIAGNOSTIC: bool = False  # reported but excluded from the weighted composite (weight 0 default)
    # One-line, human-facing explanation of what this metric measures (中文 recommended). Surfaced
    # as a hover tooltip on the metric's column header in the HTML report; "" → no tooltip.
    DESCRIPTION: str = ""

    def __init__(self, name: str | None = None, weight: float | None = None) -> None:
        # Allow per-instance override (e.g. two LatencyMetric instances with
        # different names) without subclassing.
        if name is not None:
            self.NAME = name
        if weight is not None:
            self.WEIGHT = weight
        if not getattr(type(self), "NAME", None) and name is None:
            raise TypeError(f"{type(self).__name__} must set class attribute `NAME`")

    def applies_to(self, sample: Sample) -> bool:  # noqa: ARG002 — default: all
        return True

    @abstractmethod
    def score(self, sample: Sample) -> MetricResult | Awaitable[MetricResult]:
        """Score one sample. Sync or async."""

    # ----- result builders (auto-fill name/kind from the class) -----

    def quality(self, score: float | None, judgement: str = "") -> MetricResult:
        kind = self.KIND if self.KIND != "measure" else "score"
        return MetricResult(name=self.NAME, kind=kind, score=score, judgement=judgement)

    def measure(self, value: float | None, unit: str, judgement: str = "") -> MetricResult:
        return MetricResult(
            name=self.NAME, kind="measure", value=value, unit=unit, judgement=judgement
        )

    def na(self, judgement: str = "not applicable") -> MetricResult:
        kind = self.KIND if self.KIND != "measure" else "score"
        return MetricResult(name=self.NAME, kind=kind, score=None, judgement=judgement)
