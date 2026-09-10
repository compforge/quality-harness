"""One analysis owns its measurements and findings; the trace remains observation data."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from trace_harness.model.context import TraceContext
from trace_harness.model.measurement import Measurements
from trace_harness.model.node import Finding, Node

if TYPE_CHECKING:
    from trace_harness.loading.analysis import TraceAnalysis


@dataclass(frozen=True)
class AnalysisContext:
    trace: TraceContext
    measurements: Measurements = field(default_factory=Measurements)
    findings: Mapping[str, tuple[Finding, ...]] = field(default_factory=dict)

    runtime: TraceAnalysis | None = field(default=None, repr=False, compare=False)

    finding_limit: int | None = 10

    async def fact(self, node: Node, name: str):
        if self.runtime is None:
            return node.facts.get(name)
        return await self.runtime.fact(node, name)

    async def measure(self, node: Node, name: str):
        if self.runtime is None:
            return self.measurements.get(node.node_id, name)
        return await self.runtime.metric(node, name)
