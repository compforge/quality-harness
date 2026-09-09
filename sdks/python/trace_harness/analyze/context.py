"""One analysis owns its measurements and findings; the trace remains observation data."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field

from trace_harness.model.context import TraceContext
from trace_harness.model.measurement import Measurements
from trace_harness.model.node import Finding


@dataclass(frozen=True)
class AnalysisContext:
    trace: TraceContext
    measurements: Measurements = field(default_factory=Measurements)
    findings: Mapping[str, tuple[Finding, ...]] = field(default_factory=dict)
