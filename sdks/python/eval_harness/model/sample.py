"""``Sample`` + ``MetricResult`` — the contract between solver, scorer and report.

A **Sample** is the read-only view of one worksheet row that a metric scores:
the seed (query / ground_truth / dimensions) joined to the solver output
(response / retrieved / citations / raw observations). Metrics read a Sample
and never touch the Worksheet, so the metric layer stays decoupled from
storage and orchestration.

A **MetricResult** carries one of two channels, distinguished by ``kind``:

- **quality** (``kind="score"`` continuous 0~1, or ``kind="binary"`` 0/1) —
  enters the weighted overall. ``score is None`` means "not applicable / not
  run" (the metric legitimately abstained — e.g. a correctness judge on a
  refusal case); such results are dropped from aggregation, never treated as 0.
- **measurement** (``kind="measure"``) — a raw observed number with a ``unit``
  (latency ms, token count, ...). NOT normalised, NOT part of the weighted
  overall; aggregated separately as mean / p50 / p95. ``score`` is unused.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal

MetricKind = Literal["score", "binary", "measure"]


@dataclass(frozen=True, slots=True)
class MetricResult:
    name: str
    kind: MetricKind = "score"
    # quality channel
    score: float | None = None
    judgement: str = ""
    # measurement channel (kind == "measure")
    value: float | None = None
    unit: str | None = None

    @property
    def channel(self) -> Literal["quality", "measurement"]:
        return "measurement" if self.kind == "measure" else "quality"

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "kind": self.kind,
            "score": self.score,
            "judgement": self.judgement,
            "value": self.value,
            "unit": self.unit,
        }

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> MetricResult:
        return cls(
            name=d["name"],
            kind=d.get("kind", "score"),
            score=d.get("score"),
            judgement=d.get("judgement", ""),
            value=d.get("value"),
            unit=d.get("unit"),
        )


@dataclass(frozen=True, slots=True)
class Sample:
    """Read-only view of one (arm_id × case) row handed to a metric."""

    case_id: str
    arm_id: str
    query: str
    corpus: str = ""  # which corpus this case ran against (experiments may span several)
    expected_behavior: str = "answer"  # "answer" | "refuse" — contract, drives applies_to
    response: str | None = None
    ground_truth: str | None = None
    retrieved: tuple[str, ...] = ()
    citations: tuple[dict, ...] = ()
    evidence_sources: tuple[str, ...] = ()  # gold evidence (case) — for retrieval-recall
    candidate_sources: tuple[str, ...] = ()  # candidate pool this case retrieved over (⊇ evidence)
    dimensions: dict[str, str] = field(default_factory=dict)
    observations: dict[str, Any] = field(default_factory=dict)  # ttft_ms / total_ms / tokens ...
