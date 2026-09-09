"""Scoped Trace Harness composition.

``TraceHarness`` owns every executable extension used by one analysis.  Importing a domain
package is therefore not part of the pipeline semantics: the host explicitly passes that
package's ``TraceContributions`` when it creates the harness.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass, field
from pathlib import Path

from trace_harness.analyze.diagnose import diagnose as diagnose_context
from trace_harness.analyze.diagnose.detectors import BUILTIN_DETECTORS
from trace_harness.analyze.diagnose.registry import Detector, DetectorRegistry
from trace_harness.feature.builtins import BUILTIN_FEATURES
from trace_harness.feature.engine import lazy_features
from trace_harness.feature.feature import Feature
from trace_harness.feature.registry import FeatureRegistry
from trace_harness.ingest.assemble import assemble as assemble_spans
from trace_harness.ingest.sources.jaeger_file import load_jaeger_file
from trace_harness.model.agent import AgentRunIR, validate_agent_run_ir
from trace_harness.model.context import TraceContext
from trace_harness.model.node import Finding, Node
from trace_harness.model.span import NormSpan
from trace_harness.model.spec import KindSpec, SpecSet
from trace_harness.model.viewtree import NodeTreeExtractor
from trace_harness.view.engine import render as render_display_tree
from trace_harness.view.engine import render_callstack as render_callstack_view
from trace_harness.view.engine import render_md as render_markdown
from trace_harness.view.facet import Facet, RenderConfig
from trace_harness.view.facets import builtin_facets
from trace_harness.view.interactive import render_interactive as render_interactive_view
from trace_harness.view.registry import FacetRegistry


@dataclass(frozen=True)
class TraceContributions:
    """A domain or Plugin's explicit, deterministic Trace Harness extensions."""

    specs: tuple[KindSpec, ...] = field(default_factory=tuple)
    features: tuple[Feature, ...] = field(default_factory=tuple)
    detectors: tuple[Detector, ...] = field(default_factory=tuple)
    facets: tuple[Facet, ...] = field(default_factory=tuple)
    agent_run_extractor: NodeTreeExtractor[AgentRunIR] | None = None


def merge_trace_contributions(*items: TraceContributions) -> TraceContributions:
    """Compose contributions in declaration order; earlier matches keep their priority."""
    return TraceContributions(
        specs=tuple(spec for item in items for spec in item.specs),
        features=tuple(feature for item in items for feature in item.features),
        detectors=tuple(detector for item in items for detector in item.detectors),
        facets=tuple(facet for item in items for facet in item.facets),
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
        self.features = FeatureRegistry((*BUILTIN_FEATURES, *contributions.features))
        self.detectors = DetectorRegistry((*BUILTIN_DETECTORS, *contributions.detectors))
        self.facets = FacetRegistry((*builtin_facets(), *contributions.facets))

    def assemble(self, spans: dict[str, NormSpan]) -> TraceContext:
        return assemble_spans(spans, self.specs, feature_registry=self.features)

    def build_context(self, path: str | Path) -> TraceContext:
        path = Path(path)
        context = self.assemble(load_jaeger_file(path))
        context.evidence_dir = path.parent / context.trace_id
        return context

    def diagnose(self, context: TraceContext, *, probes: bool = False) -> dict[str, list[Finding]]:
        return diagnose_context(
            context,
            probes=probes,
            detector_registry=self.detectors,
        )

    def lazy_features(self, node: Node, context: TraceContext) -> dict:
        return lazy_features(
            node,
            context.view(),
            context.raw_attr,
            registry=self.features,
        )

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

    def render_interactive(
        self,
        context: TraceContext,
        findings: dict[str, list[Finding]] | None = None,
    ) -> str:
        return render_interactive_view(
            context,
            findings,
            facet_registry=self.facets,
            feature_registry=self.features,
            agent_run_ir=self.extract_agent_runs(context),
        )

    def render_md(
        self,
        context: TraceContext,
        findings: dict[str, list[Finding]] | None = None,
        *,
        prune_below_ms: float | None = None,
    ) -> str:
        return render_markdown(
            context,
            findings,
            prune_below_ms=prune_below_ms,
            registry=self.facets,
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
