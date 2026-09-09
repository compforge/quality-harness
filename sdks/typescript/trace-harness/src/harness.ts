import { builtinDetectors } from "./analyze/detectors";
import { diagnose, type Findings } from "./analyze/diagnose";
import { DetectorRegistry, type Detector } from "./analyze/registry";
import { builtinTransforms, type FactTransform } from "./transform";
import { AnalysisContext } from "./analyze/context";
import { measure, builtinMeasurers, type Measurer } from "./analyze/measure";
import type { Measurements } from "./model/measurement";
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
  transforms?: Iterable<FactTransform>;
  measurers?: Iterable<Measurer>;
  detectors?: Iterable<Detector>;
  facets?: Iterable<Facet>;
  agentRunExtractor?: NodeTreeExtractor<AgentRunIR>;
}

export function mergeTraceContributions(...items: TraceContributions[]): TraceContributions {
  return {
    specs: items.flatMap((item) => [...(item.specs ?? [])]),
    transforms: items.flatMap((item) => [...(item.transforms ?? [])]),
    measurers: items.flatMap((item) => [...(item.measurers ?? [])]),
    detectors: items.flatMap((item) => [...(item.detectors ?? [])]),
    facets: items.flatMap((item) => [...(item.facets ?? [])]),
    agentRunExtractor: items.find((item) => item.agentRunExtractor)?.agentRunExtractor,
  };
}

/** Owns the complete, scoped executable configuration for trace analysis. */
export class TraceHarness {
  readonly specs: SpecSet;
  readonly transforms: FactTransform[];
  readonly measurers: Measurer[];
  readonly detectors: DetectorRegistry;
  readonly facets: FacetRegistry;

  constructor(readonly contributions: TraceContributions) {
    this.specs = new SpecSet(contributions.specs ?? []);
    this.transforms = [...builtinTransforms(), ...(contributions.transforms ?? [])];
    this.measurers = [...builtinMeasurers(), ...(contributions.measurers ?? [])];
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
    return assemble(spans, this.specs, this.transforms);
  }

  measure(context: TraceContext): Measurements { return measure(context, this.measurers); }

  diagnose(context: TraceContext, measurements: Measurements = this.measure(context)): Findings {
    return diagnose(context, this.detectors, measurements);
  }

  analyze(context: TraceContext, diagnosis = true): AnalysisContext {
    const measurements = this.measure(context);
    return new AnalysisContext(context, measurements, diagnosis ? this.diagnose(context, measurements) : {});
  }

  transform(node: Node, context: TraceContext, ...names: string[]): Record<string, unknown> {
    context.transforms?.materialize(names.map((name) => [node, name] as const));
    return Object.fromEntries(names.filter((name) => Object.hasOwn(node.facts, name)).map((name) => [name, node.facts[name]]));
  }

  transformAll(context: TraceContext, ...names: string[]): void {
    context.transforms?.materialize(context.nodes.flatMap((node) => names.map((name) => [node, name] as const)));
  }

  extractAgentRuns(context: TraceContext): AgentRunIR | undefined {
    const extractor = this.contributions.agentRunExtractor;
    return extractor ? validateAgentRunIR(extractor.extract(context), context) : undefined;
  }

  renderDisplay(
    context: TraceContext,
    findings: Readonly<Record<string, readonly Finding[]>> = {},
    config: RenderConfig = {},
  ): DisplayNode[] {
    return renderDisplay(context.view(), findings, this.facets, config);
  }

  renderInteractive(
    context: TraceContext,
    findings: Readonly<Record<string, readonly Finding[]>> = {},
    options: { measurements?: Measurements } = {},
  ): string {
    return renderInteractive(context, findings, {
      ...options,
      facetRegistry: this.facets,
      agentRunIR: this.extractAgentRuns(context),
    });
  }
}
