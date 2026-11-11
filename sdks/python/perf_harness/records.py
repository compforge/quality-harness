"""Perf admission facts; actual calls and Outcomes remain owned by common OperationRun."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal


@dataclass(frozen=True)
class RequestEvaluation:
    result: Literal["success", "failure", "rejected", "timeout"]
    error_kind: str | None = None

    def __post_init__(self) -> None:
        if self.result not in {"success", "failure", "rejected", "timeout"}:
            raise ValueError(f"invalid request result: {self.result!r}")

    @property
    def ok(self) -> bool:
        return self.result == "success"


@dataclass
class RequestRecord:
    id: str
    case_id: str
    scheduled_at: float
    arrived_at: float
    dispatched_at: float | None = None
    finished_at: float | None = None
    state: Literal["arrived", "dispatched", "finished", "dropped", "interrupted"] = "arrived"
    reason: str | None = None
    operation_run_id: str | None = None
    facets: dict[str, str] = field(default_factory=dict)
