"""Scoped Trace Harness composition.

``TraceHarness`` owns every executable extension used by one analysis.  Importing a domain
package is therefore not part of the pipeline semantics: the host explicitly passes that
package's ``TraceContributions`` when it creates the harness.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING

from trace_harness.ingest.sources.base import Source
from trace_harness.loading.facts import FactProducer
from trace_harness.loading.model import LoadConfig

if TYPE_CHECKING:
    from trace_harness.batch import BatchDetector
    from trace_harness.runtime import TraceSession

from trace_harness.analyze.context import AnalysisContext
from trace_harness.analyze.diagnose import diagnose as diagnose_context
from trace_harness.analyze.diagnose.detectors import BUILTIN_DETECTORS
from trace_harness.analyze.diagnose.registry import Detector, DetectorRegistry
from trace_harness.analyze.measure import BUILTIN_MEASURERS, Measurer, measure
from trace_harness.ingest.assemble import assemble as assemble_spans
from trace_harness.ingest.sources.jaeger_file import load_jaeger_file
from trace_harness.model.agent import AgentRunIR, validate_agent_run_ir
from trace_harness.model.context import TraceContext
from trace_harness.model.measurement import Measurements
from trace_harness.model.node import Finding, Node
from trace_harness.model.span import NormSpan
from trace_harness.model.spec import KindSpec, SpecSet
from trace_harness.model.viewtree import NodeTreeExtractor
from trace_harness.transform import BUILTIN_TRANSFORMS, FactTransform
from trace_harness.view.engine import render as render_display_tree
from trace_harness.view.engine import render_callstack as render_callstack_view
from trace_harness.view.engine import render_md as render_markdown
from trace_harness.view.facet import Facet, RenderConfig
from trace_harness.view.facets import builtin_facets
from trace_harness.view.interactive import render_interactive as render_interactive_view
from trace_harness.view.measurements import MeasurementFilter, filter_measurements
from trace_harness.view.registry import FacetRegistry


@dataclass(frozen=True)
class TraceContributions:
    """A domain or Plugin's explicit, deterministic Trace Harness extensions."""

    structure_fields: tuple[str, ...] = ()
    field_aliases: dict[str, str] = field(default_factory=dict)
    normalize_span: Callable[[NormSpan], NormSpan] | None = None
    prepare_spans: Callable[[dict[str, NormSpan]], dict[str, NormSpan]] | None = None
    fact_producers: tuple[FactProducer, ...] = ()
    batch_detectors: tuple[BatchDetector, ...] = ()
    specs: tuple[KindSpec, ...] = field(default_factory=tuple)
    transforms: tuple[FactTransform, ...] = ()
    measurers: tuple[Measurer, ...] = ()
    detectors: tuple[Detector, ...] = field(default_factory=tuple)
    facets: tuple[Facet, ...] = field(default_factory=tuple)
    agent_run_extractor: NodeTreeExtractor[AgentRunIR] | None = None
    measurement_filter: MeasurementFilter | None = None


def merge_trace_contributions(*items: TraceContributions) -> TraceContributions:
    """Compose contributions in declaration order; earlier matches keep their priority."""
    return TraceContributions(
        field_aliases={k: v for item in reversed(items) for k, v in item.field_aliases.items()},
        normalize_span=next(
            (item.normalize_span for item in items if item.normalize_span is not None), None
        ),
        structure_fields=tuple(dict.fromkeys(f for item in items for f in item.structure_fields)),
        prepare_spans=next(
            (item.prepare_spans for item in items if item.prepare_spans is not None), None
        ),
        fact_producers=tuple(p for item in items for p in item.fact_producers),
        batch_detectors=tuple(d for item in items for d in item.batch_detectors),
        specs=tuple(spec for item in items for spec in item.specs),
        transforms=tuple(t for item in items for t in item.transforms),
        measurers=tuple(m for item in items for m in item.measurers),
        detectors=tuple(detector for item in items for detector in item.detectors),
        facets=tuple(facet for item in items for facet in item.facets),
        measurement_filter=next(
            (item.measurement_filter for item in items if item.measurement_filter is not None),
            None,
        ),
        agent_run_extractor=next(
            (item.agent_run_extractor for item in items if item.agent_run_extractor is not None),
            None,
        ),
    )


class TraceHarness:
    """State owner for one trace-analysis configuration.

    The object is reusable across traces, but its registries never leak into another harness.
    Probe execution remains an explicit ``diagnose(..., probes=True)`` host decision.
    """

    def __init__(self, contributions: TraceContributions) -> None:
        self.contributions = contributions
        self.specs = SpecSet(list(contributions.specs))
        self.transforms = (*BUILTIN_TRANSFORMS, *contributions.transforms)
        self.measurers = (*BUILTIN_MEASURERS, *contributions.measurers)
        self.detectors = DetectorRegistry((*BUILTIN_DETECTORS, *contributions.detectors))
        self.facets = FacetRegistry((*builtin_facets(), *contributions.facets))

    def open(
        self,
        source: Source,
        *,
        work_dir: str | Path | None = None,
        config: LoadConfig | None = None,
    ) -> TraceSession:
        """Open managed loading resources; use as an async context manager."""
        from trace_harness.runtime import TraceSession

        return TraceSession(self, source, work_dir=work_dir, config=config)

    def assemble(self, spans: dict[str, NormSpan]) -> TraceContext:
        return assemble_spans(spans, self.specs, transforms=self.transforms)

    def build_context(self, path: str | Path) -> TraceContext:
        path = Path(path)
        context = self.assemble(load_jaeger_file(path))
        context.evidence_dir = path.parent / context.trace_id
        return context

    async def diagnose(
        self,
        context: TraceContext,
        *,
        probes: bool = False,
        measurements: Measurements | None = None,
    ) -> dict[str, list[Finding]]:
        return await diagnose_context(
            context,
            probes=probes,
            detector_registry=self.detectors,
            measurements=measurements if measurements is not None else self.measure(context),
        )

    def measure(self, context: TraceContext) -> Measurements:
        return measure(context, self.measurers)

    async def analyze(
        self, context: TraceContext, *, diagnosis: bool = True, probes: bool = False
    ) -> AnalysisContext:
        measurements = self.measure(context)
        findings = (
            await self.diagnose(context, measurements=measurements, probes=probes)
            if diagnosis
            else {}
        )
        return AnalysisContext(
            context, measurements, {key: tuple(value) for key, value in findings.items()}
        )

    def transform(self, node: Node, context: TraceContext, *names: str) -> dict:
        """Materialize requested facts and dependencies in the trace's own context."""
        if context.transforms is not None:
            context.transforms.materialize((node, name) for name in names)
        return {name: node.facts[name] for name in names if name in node.facts}

    def transform_all(self, context: TraceContext, *names: str) -> None:
        """Prepare explicitly selected facts before analysis or rendering."""
        if context.transforms is not None:
            context.transforms.materialize((node, name) for node in context.nodes for name in names)

    def extract_agent_runs(self, context: TraceContext) -> AgentRunIR | None:
        extractor = self.contributions.agent_run_extractor
        if extractor is None:
            return None
        return validate_agent_run_ir(extractor.extract(context), context)

    def render_display(
        self,
        context: TraceContext,
        findings: dict[str, list[Finding]] | None = None,
        *,
        config: RenderConfig | None = None,
    ):
        return render_display_tree(
            context.view(),
            findings,
            registry=self.facets,
            config=config,
        )

    def visible_measurements(
        self, context: TraceContext, measurements: Measurements
    ) -> Measurements:
        """Project prepared measurements for reports; analysis remains complete."""
        return filter_measurements(context, measurements, self.contributions.measurement_filter)

    def render_interactive(
        self,
        context: TraceContext,
        findings: dict[str, list[Finding]] | None = None,
        *,
        measurements: Measurements | None = None,
    ) -> str:
        return render_interactive_view(
            context,
            findings,
            facet_registry=self.facets,
            measurements=(
                self.visible_measurements(context, measurements)
                if measurements is not None
                else None
            ),
            agent_run_ir=self.extract_agent_runs(context),
        )

    def render_md(
        self,
        context: TraceContext,
        findings: dict[str, list[Finding]] | None = None,
        *,
        prune_below_ms: float | None = None,
        measurements: Measurements | None = None,
    ) -> str:
        from trace_harness.view.measurements import measurements_md

        return render_markdown(
            context,
            findings,
            prune_below_ms=prune_below_ms,
            registry=self.facets,
        ) + measurements_md(
            context,
            self.visible_measurements(context, measurements) if measurements is not None else None,
        )

    def render_callstack(
        self,
        context: TraceContext,
        findings: dict[str, list[Finding]] | None = None,
        *,
        node_threshold: int = 120,
    ) -> str:
        return render_callstack_view(
            context,
            findings,
            node_threshold=node_threshold,
            registry=self.facets,
        )


def contributions_from_specs(specs: Iterable[KindSpec]) -> TraceContributions:
    """Small adapter for existing ``SpecSet``/iterable producers."""
    return TraceContributions(specs=tuple(specs))
