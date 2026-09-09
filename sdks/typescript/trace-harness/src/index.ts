export { builtinDetectors } from "./analyze/detectors";
export { diagnose, type Findings } from "./analyze/diagnose";
export { DetectorRegistry, type Detector } from "./analyze/registry";
export * from "./feature";
export { TraceHarness, mergeTraceContributions, type TraceContributions } from "./harness";
export { assemble } from "./ingest/assemble";
export { normalizeJaegerSpan, normalizeJaegerSpans } from "./ingest/jaeger";
export { SERVICE_KIND, durationMetric, formatBytes, formatMs, serviceSpec } from "./kinds/base";
export { genAiSpecs } from "./kinds/genai";
export {
  ANALYSIS_SCHEMA,
  analysisSnapshot,
  type AnalysisFinding,
  type AnalysisNode,
  type AnalysisSnapshot,
} from "./model/analysis";
export { TraceContext } from "./model/context";
export {
  AGENT_RUN_SCHEMA,
  agentRunSnapshot,
  createAgentRunIR,
  validateAgentRunIR,
  type AgentRun,
  type AgentRunIR,
  type AgentRunItem,
  type AgentTurn,
  type ModelCall,
  type Operation,
  type ToolCall,
  type TurnItem,
} from "./model/agent";
export { Node, type Emphasis, type Field, type Finding, type NodeInit, type Severity } from "./model/node";
export { SpecSet, mergeSpecs, type KindSpec } from "./model/spec";
export { NormSpan, spanErrorText, type ErrorEvent, type SpanAttributes, type SpanEvent } from "./model/span";
export { ViewTree, buildView, type NodeTreeExtractor } from "./model/viewtree";
export { DisplayName, nameProjections, type Compact, type DisplayNode } from "./view/display";
export {
  DefaultFacet,
  Facet,
  type ChildOp,
  type PerspectiveLevel,
  type RenderConfig,
  type RenderContext,
  type TracePerspective,
} from "./view/facet";
export { builtinFacets } from "./view/facets";
export { renderDisplay } from "./view/engine";
export { renderInteractive } from "./view/interactive";
export { FacetRegistry } from "./view/registry";
