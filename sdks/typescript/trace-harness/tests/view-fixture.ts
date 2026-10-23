import { AnalysisContext, Measurements, TraceHarness, render } from "../src";
import type { TraceContext, Finding, AgentRunIR } from "../src";

// Tests that exercise the complete report explicitly prepare and compose both views.
export function reportHtml(harness: TraceHarness, trace: TraceContext, findings: Record<string, readonly Finding[]> = {}, options: { measurements?: Measurements; agentRunIR?: AgentRunIR } = {}): string {
  const analysis = new AnalysisContext(trace, options.measurements ? new Measurements(structuredClone(options.measurements.specs), structuredClone(options.measurements.sources), structuredClone(options.measurements.nodes), structuredClone(options.measurements.trace)) : undefined, findings);
  harness.prepare(analysis, { agentRuns: true,
    callBreakdowns: Object.keys(harness.visibleMeasurements(trace, analysis.measurements).nodes) });
  if (options.agentRunIR) analysis.agentRuns = options.agentRunIR;
  const views = [harness.buildView(analysis, { scope: "trace" })];
  if (analysis.agentRuns) views.push(harness.buildView(analysis, { scope: "trace", view: "agent" }));
  return render(views as import("../src").TraceView[], { format: "html" });
}
