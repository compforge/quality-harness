import { TraceSession, type SessionOptions } from "./runtime";
import type { Source } from "./ingest/sources/base";
import type { FactProducer } from "./loading/facts";
import { builtinDetectors } from "./analyze/detectors";
import { diagnose, type Findings } from "./analyze/diagnose";
import { DetectorRegistry, type Detector } from "./analyze/registry";
import { builtinTransforms, type FactTransform } from "./transform";
import { AnalysisContext } from "./analyze/context";
import { measure, builtinMeasurers, type Measurer } from "./analyze/measure";
import type { Measurements } from "./model/measurement";
import { assemble } from "./ingest/assemble";
import type { SessionView } from "@compforge/agent-observation";
import { validateSessionViews } from "./observation";
import type { TraceContext } from "./model/context";
import type { Finding, Node } from "./model/node";
import type { NormSpan } from "./model/span";
import { SpecSet, type KindSpec } from "./model/spec";
import type { NodeTreeExtractor } from "./model/node-tree";
import type { Facet } from "./view/facet";
import { builtinFacets } from "./view/facets";
import { buildView } from "./view/build";
import type { View, ViewRequest } from "./view/model";
import { prepareCallBreakdown } from "./analyze/call-breakdown";
import { FacetRegistry } from "./view/registry";
import { filterMeasurements, type MeasurementFilter } from "./view/measurements";

export interface TraceContributions {
  structureFields?: readonly string[];
  fieldAliases?: Readonly<Record<string, string>>;
  normalizeSpan?(span: NormSpan): NormSpan;
  prepareSpans?(spans: Map<string, NormSpan>): Map<string, NormSpan>;
  factProducers?: readonly FactProducer[];
  specs?: Iterable<KindSpec>;
  transforms?: Iterable<FactTransform>;
  measurers?: Iterable<Measurer>;
  detectors?: Iterable<Detector>;
  facets?: Iterable<Facet>;
  sessionViewExtractor?: NodeTreeExtractor<SessionView[]>;
  measurementFilter?: MeasurementFilter;
}

export function mergeTraceContributions(...items: TraceContributions[]): TraceContributions {
  return {
    structureFields: [...new Set(items.flatMap(item => item.structureFields ?? []))],
    fieldAliases: Object.assign({}, ...[...items].reverse().map(item => item.fieldAliases ?? {})),
    normalizeSpan: items.find(item => item.normalizeSpan)?.normalizeSpan,
    prepareSpans: items.find(item => item.prepareSpans)?.prepareSpans,
    factProducers: items.flatMap(item => item.factProducers ?? []),
    specs: items.flatMap((item) => [...(item.specs ?? [])]),
    transforms: items.flatMap((item) => [...(item.transforms ?? [])]),
    measurers: items.flatMap((item) => [...(item.measurers ?? [])]),
    detectors: items.flatMap((item) => [...(item.detectors ?? [])]),
    facets: items.flatMap((item) => [...(item.facets ?? [])]),
    measurementFilter: items.find((item) => item.measurementFilter)?.measurementFilter,
    sessionViewExtractor: items.find((item) => item.sessionViewExtractor)?.sessionViewExtractor,
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

  open(source: Source, options: SessionOptions = {}): TraceSession {
    return new TraceSession(this, source, options);
  }

  assemble(spans: Map<string, NormSpan>): TraceContext {
    return assemble(spans, this.specs, this.transforms);
  }

  measure(context: TraceContext): Measurements { return measure(context, this.measurers); }

  async diagnose(context: TraceContext, measurements: Measurements = this.measure(context)): Promise<Findings> {
    return diagnose(context, this.detectors, measurements);
  }

  async analyze(context: TraceContext, diagnosis = true): Promise<AnalysisContext> {
    const measurements = this.measure(context);
    return new AnalysisContext(context, measurements, diagnosis ? await this.diagnose(context, measurements) : {});
  }

  transform(node: Node, context: TraceContext, ...names: string[]): Record<string, unknown> {
    context.transforms?.materialize(names.map((name) => [node, name] as const));
    return Object.fromEntries(names.filter((name) => Object.hasOwn(node.facts, name)).map((name) => [name, node.facts[name]]));
  }

  transformAll(context: TraceContext, ...names: string[]): void {
    context.transforms?.materialize(context.nodes.flatMap((node) => names.map((name) => [node, name] as const)));
  }

  extractSessionViews(context: TraceContext): SessionView[] | undefined {
    const extractor = this.contributions.sessionViewExtractor;
    return extractor ? validateSessionViews(extractor.extract(context), context) : undefined;
  }

  /** Report projection only: detectors and saved analysis retain all measurements. */
  visibleMeasurements(context: TraceContext, measurements: Measurements): Measurements {
    return filterMeasurements(context, measurements, this.contributions.measurementFilter);
  }

  /** Prepare derived data explicitly, independently of any display selection or format. */
  prepare(analysis: AnalysisContext, options: { sessionViews?: boolean; callBreakdowns?: readonly string[] }): AnalysisContext {
    if (options.sessionViews && !analysis.sessionViews) analysis.sessionViews = this.extractSessionViews(analysis.trace);
    for (const nodeId of options.callBreakdowns ?? []) prepareCallBreakdown(analysis.trace, analysis.measurements, nodeId);
    return analysis;
  }

  buildView(analysis: AnalysisContext, request: ViewRequest): View {
    return buildView(analysis, request, { facets: this.facets, measurementFilter: this.contributions.measurementFilter });
  }
}
