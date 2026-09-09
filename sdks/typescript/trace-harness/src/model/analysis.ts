import type { Findings } from "../analyze/diagnose";
import type { TraceContext } from "./context";
import type { Emphasis, Severity } from "./node";

export const ANALYSIS_SCHEMA = "trace-harness/analysis@1";

export interface AnalysisNode {
  node_id: string;
  parent_node_id: string | null;
  kind: string;
  name: string;
  start_ms: number;
  duration_ms: number;
  service: string | null;
  primary_span_id: string;
  span_ids: string[];
  error_span_ids: string[];
  error_text: string;
  facts: Record<string, unknown>;
  brief: Array<{ label: string; value: string; emphasis: Emphasis }>;
}

export interface AnalysisFinding {
  ref: string | null;
  source: string;
  severity: Severity;
  scope: "node" | "trace" | "cohort";
  rank: number | null;
  note: string;
  data: Record<string, unknown>;
  symptoms: string[];
  causes: string[];
}

export interface AnalysisSnapshot {
  schema: typeof ANALYSIS_SCHEMA;
  trace_id: string;
  span_count: number;
  nodes: AnalysisNode[];
  findings: AnalysisFinding[];
}

export function analysisSnapshot(
  context: TraceContext,
  findings: Findings = {},
): AnalysisSnapshot {
  const nodes = [...context.nodes]
    .sort((left, right) => left.start_ms - right.start_ms || left.node_id.localeCompare(right.node_id))
    .map((node): AnalysisNode => ({
      node_id: node.node_id,
      parent_node_id: node.parent_node_id ?? null,
      kind: node.kind,
      name: node.name,
      start_ms: node.start_ms,
      duration_ms: node.duration_ms,
      service: node.service ?? null,
      primary_span_id: node.primary_span_id,
      span_ids: [...node.span_ids],
      error_span_ids: [...node.error_span_ids],
      error_text: node.error_text,
      facts: node.facts,
      brief: node.brief.map((item) => ({
        label: item.label,
        value: item.value,
        emphasis: item.emphasis ?? "normal",
      })),
    }));
  const flattened = Object.values(findings).flat().sort((left, right) => {
    const leftKey = [left.scope ?? "node", left.ref ?? "", left.source, left.severity, left.note ?? ""];
    const rightKey = [right.scope ?? "node", right.ref ?? "", right.source, right.severity, right.note ?? ""];
    return leftKey.join("\u0000").localeCompare(rightKey.join("\u0000"));
  });
  return {
    schema: ANALYSIS_SCHEMA,
    trace_id: context.trace_id,
    span_count: context.span_count,
    nodes,
    findings: flattened.map((finding): AnalysisFinding => ({
      ref: finding.ref ?? null,
      source: finding.source,
      severity: finding.severity,
      scope: finding.scope ?? "node",
      rank: finding.rank ?? null,
      note: finding.note ?? "",
      data: finding.data ?? {},
      symptoms: [...(finding.symptoms ?? [])],
      causes: [...(finding.causes ?? [])],
    })),
  };
}
