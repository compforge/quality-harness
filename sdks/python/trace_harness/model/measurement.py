"""Trace-local Measurement values: descriptive evidence, never a verdict."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal


@dataclass(frozen=True)
class MeasurementSpec:
    id: str
    scope: Literal["node", "trace_prefix"]
    units: dict[str, str]
    description: str
    dimensions: tuple[str, ...] = ()


@dataclass(frozen=True)
class CallSource:
    id: str
    kind: str
    start_ms: float
    end_ms: float
    node_id: str
    span_ids: tuple[str, ...]


@dataclass(frozen=True)
class Measurement:
    spec_id: str
    anchor_node_id: str
    status: Literal["measured", "not_applicable", "error"]
    values: dict = field(default_factory=dict)
    evidence: dict = field(default_factory=dict)
    error: str | None = None


@dataclass
class Measurements:
    specs: list[MeasurementSpec] = field(default_factory=list)
    sources: list[CallSource] = field(default_factory=list)
    results: dict[str, list[Measurement]] = field(default_factory=dict)

    def get(self, node_id: str, spec_id: str) -> Measurement | None:
        return next((m for m in self.results.get(node_id, ()) if m.spec_id == spec_id), None)
