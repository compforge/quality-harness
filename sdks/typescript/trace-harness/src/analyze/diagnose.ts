import { measure } from "./measure";
import type { Measurements } from "../model/measurement";
import { AnalysisContext } from "./context";
import type { TraceContext } from "../model/context";
import type { Finding, Node } from "../model/node";
import { builtinDetectors } from "./detectors";
import { DetectorRegistry } from "./registry";

export type Findings = Record<string, Finding[]>;

function append(findings: Findings, finding: Finding): void {
  if (!finding.ref) return;
  (findings[finding.ref] ??= []).push(finding);
}

function postOrder(context: TraceContext): Node[] {
  const result: Node[] = [];
  const seen = new Set<string>();
  const visit = (node: Node): void => {
    if (seen.has(node.node_id)) return;
    seen.add(node.node_id);
    for (const child of context.view().children(node)) visit(child);
    result.push(node);
  };
  for (const root of context.view().roots) visit(root);
  return result;
}

/** Deterministic node findings: physical errors, per-kind rules, then post-order detectors. */
export async function diagnose(
  context: TraceContext,
  detectorRegistry?: DetectorRegistry,
  measurements: Measurements = measure(context),
): Promise<Findings> {
  return (await diagnoseAnalysis(new AnalysisContext(context, measurements), detectorRegistry)).findings as Findings;
}

export async function diagnoseAnalysis(analysis: AnalysisContext, detectorRegistry?: DetectorRegistry): Promise<AnalysisContext> {
  analysis.runtime?.check();
  const context = analysis.trace;
  const activeRegistry = detectorRegistry ?? new DetectorRegistry(builtinDetectors());
  const findings: Findings = {};
  for (const node of context.nodes) {
    if (node.has_error) {
      append(findings, {
        ref: node.node_id,
        source: "error",
        severity: "error",
        note: node.error_text,
      });
    }
    for (const rule of context.specs.get(node.kind)?.rules ?? []) {
      for (const finding of await rule(node, analysis)) append(findings, finding);
    }
  }
  for (const node of postOrder(context)) {
    for (const detector of activeRegistry.registered()) {
      for (const finding of await detector(node, new AnalysisContext(context, analysis.measurements, findings, analysis.runtime))) append(findings, finding);
    }
  }
  analysis.runtime?.check();
  return new AnalysisContext(context, analysis.measurements, findings, analysis.runtime);
}
