import { builtinDetectors } from "./analyze/detectors";
import { diagnose, type Findings } from "./analyze/diagnose";
import { DetectorRegistry, type Detector } from "./analyze/registry";
import { builtinFeatures, lazyFeatures, type Feature } from "./feature";
import { FeatureRegistry } from "./feature/registry";
import { assemble } from "./ingest/assemble";
import { validateAgentRunIR, type AgentRunIR } from "./model/agent";
import type { TraceContext } from "./model/context";
import type { Finding, Node } from "./model/node";
import type { NormSpan } from "./model/span";
import { SpecSet, type KindSpec } from "./model/spec";
import type { NodeTreeExtractor } from "./model/viewtree";
import type { DisplayNode } from "./view/display";
import { renderDisplay } from "./view/engine";
import type { Facet, RenderConfig } from "./view/facet";
import { builtinFacets } from "./view/facets";
import { renderInteractive } from "./view/interactive";
import { FacetRegistry } from "./view/registry";

export interface TraceContributions {
  specs?: Iterable<KindSpec>;
  features?: Iterable<Feature>;
  detectors?: Iterable<Detector>;
  facets?: Iterable<Facet>;
  agentRunExtractor?: NodeTreeExtractor<AgentRunIR>;
}

export function mergeTraceContributions(...items: TraceContributions[]): TraceContributions {
  return {
    specs: items.flatMap((item) => [...(item.specs ?? [])]),
    features: items.flatMap((item) => [...(item.features ?? [])]),
    detectors: items.flatMap((item) => [...(item.detectors ?? [])]),
    facets: items.flatMap((item) => [...(item.facets ?? [])]),
    agentRunExtractor: items.find((item) => item.agentRunExtractor)?.agentRunExtractor,
  };
}

/** Owns the complete, scoped executable configuration for trace analysis. */
export class TraceHarness {
  readonly specs: SpecSet;
  readonly features: FeatureRegistry;
  readonly detectors: DetectorRegistry;
  readonly facets: FacetRegistry;

  constructor(readonly contributions: TraceContributions) {
    this.specs = new SpecSet(contributions.specs ?? []);
    this.features = new FeatureRegistry([
      ...builtinFeatures(),
      ...(contributions.features ?? []),
    ]);
    this.detectors = new DetectorRegistry([
      ...builtinDetectors(),
      ...(contributions.detectors ?? []),
    ]);
    this.facets = new FacetRegistry([
      ...builtinFacets(),
      ...(contributions.facets ?? []),
    ]);
  }

  assemble(spans: Map<string, NormSpan>): TraceContext {
    return assemble(spans, this.specs, this.features);
  }

  diagnose(context: TraceContext): Findings {
    return diagnose(context, this.detectors);
  }

  lazyFeatures(node: Node, context: TraceContext): Record<string, unknown> {
    return lazyFeatures(
      node,
      context.view(),
      (spanId) => context.raw_attr(spanId),
      this.features,
    );
  }

  extractAgentRuns(context: TraceContext): AgentRunIR | undefined {
    const extractor = this.contributions.agentRunExtractor;
    return extractor ? validateAgentRunIR(extractor.extract(context), context) : undefined;
  }

  renderDisplay(
    context: TraceContext,
    findings: Record<string, Finding[]> = {},
    config: RenderConfig = {},
  ): DisplayNode[] {
    return renderDisplay(context.view(), findings, this.facets, config);
  }

  renderInteractive(
    context: TraceContext,
    findings: Record<string, Finding[]> = {},
  ): string {
    return renderInteractive(context, findings, {
      featureRegistry: this.features,
      facetRegistry: this.facets,
      agentRunIR: this.extractAgentRuns(context),
    });
  }
}
