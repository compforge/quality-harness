"""Trace-local Measurement values: descriptive evidence, never a verdict."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal


@dataclass(frozen=True)
class MeasurementSpec:
    kind: str
    scope: Literal["node", "trace"]
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
    kind: str
    node_id: str | None
    status: Literal["measured", "not_applicable", "error"]
    values: dict = field(default_factory=dict)
    evidence: dict = field(default_factory=dict)
    error: str | None = None


@dataclass
class Measurements:
    specs: list[MeasurementSpec] = field(default_factory=list)
    sources: list[CallSource] = field(default_factory=list)
    nodes: dict[str, list[Measurement]] = field(default_factory=dict)

    trace: list[Measurement] = field(default_factory=list)

    def get(self, node_id: str | None, kind: str) -> Measurement | None:
        return next(
            (
                m
                for m in (self.trace if node_id is None else self.nodes.get(node_id, ()))
                if m.kind == kind
            ),
            None,
        )
