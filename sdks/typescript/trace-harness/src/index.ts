export { builtinDetectors } from "./analyze/detectors";
export { diagnose, type Findings } from "./analyze/diagnose";
export { DetectorRegistry, type Detector } from "./analyze/registry";
export * from "./transform";
export * from "./model/measurement";
export { AnalysisContext } from "./analyze/context";
export { measure, type Measurer } from "./analyze/measure";
export { type MeasurementFilter } from "./view/measurements";
export { TraceHarness, mergeTraceContributions, type TraceContributions } from "./harness";
export { assemble } from "./ingest/assemble";
export { normalizeJaegerSpan, normalizeJaegerSpans } from "./ingest/jaeger";
export { SERVICE_KIND, durationMetric, formatBytes, formatMs, serviceSpec } from "./kinds/base";
export { genAiSpecs } from "./kinds/genai";
export {
  ANALYSIS_SCHEMA,
  analysisSnapshot,
  loadAnalysis,
  type AnalysisFinding,
  type AnalysisNode,
  type AnalysisSnapshot,
} from "./model/analysis";
export { TraceContext } from "./model/context";
export { Node, type Emphasis, type Field, type Finding, type NodeInit, type Severity } from "./model/node";
export { SpecSet, mergeSpecs, type KindSpec } from "./model/spec";
export { NormSpan, spanErrorText, type ErrorEvent, type SpanAttributes, type SpanEvent } from "./model/span";
export { NodeTree, buildNodeTree, type NodeTreeExtractor } from "./model/node-tree";
export { DisplayName, nameProjections, type Compact, type DisplayNode } from "./view/display";
export {
  DefaultFacet,
  Facet,
  type ChildOp,
  type RenderConfig,
  type RenderContext,
} from "./view/facet";
export { builtinFacets } from "./view/facets";
export { render, type ViewFormat } from "./view/render";
export { buildView } from "./view/build";
export type { View, ViewRequest, TraceView, NodeView, TraceViewKind, NodeViewKind, ViewRow, ViewPage } from "./view/model";
export { type CallBreakdown } from "./analyze/call-breakdown";
export { FacetRegistry } from "./view/registry";

export { TraceSession, TraceLease, type SessionOptions } from "./runtime";
export { Dataset } from "./dataset";
export { type Source, type SpanQuery } from "./ingest/sources/base";
export { type LoadConfig, type EvidenceRef, EvidenceMissing, EvidenceTooLarge } from "./loading/model";
export { EvidenceDependency, FactDependency, type Dependency, type FactProducer } from "./loading/facts";
export { JaegerFileSource } from "./ingest/sources/jaeger-file";
