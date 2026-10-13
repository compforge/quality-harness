import { deriveCallBreakdown } from "../src/analyze/call-breakdown";
import { expect, test } from "bun:test";
import { readFileSync } from "node:fs";
import { runInNewContext } from "node:vm";
import { createAgentRunIR, AnalysisContext, buildView, render,
  Measurements, Node, TraceContext, TraceHarness, genAiSpecs, normalizeJaegerSpans, analysisSnapshot, loadAnalysis } from "../src";
import { MEASUREMENT_DIALOG_SCRIPT } from "../src/view/measurement-dialog";
import { measurementWindow, aggregateMeasurementCalls } from "../src/analyze/call-breakdown";
import { archiveEntries } from "./archive-fixture";

const fixture = JSON.parse(readFileSync(new URL('../../../../conformance/trace/fixtures/inspection-window.json', import.meta.url), 'utf8'));
function prepared() {
  const nodes = fixture.calls.map((call: any) => new Node({
    node_id: call.node_id, kind: call.kind, name: call.name, primary_span_id: call.span_ids[0], span_ids: call.span_ids,
    facts: {}, service: call.service, start_ms: call.start_ms, duration_ms: call.end_ms - call.start_ms,
  }));
  const trace = new TraceContext('offline', new Map(), nodes, new Map());
  const spec = { kind: 'calls_until_node_end', scope: 'node' as const, dimensions: ['call_kind'], units: { count: 'calls' }, description: 'prefix' };
  const result = { kind: spec.kind, node_id: 'anchor', status: 'measured' as const,
    values: { node: { count: 4 } }, evidence: { source: 'calls', start_ms: fixture.window[0], end_ms: fixture.window[1] }, error: null };
  return { trace, measurements: new Measurements([spec], fixture.calls, { anchor: [result] }) };
}

function analysisWithTiming(trace: TraceContext, measurements: Measurements): AnalysisContext {
  const analysis = new AnalysisContext(trace, new Measurements(structuredClone(measurements.specs), structuredClone(measurements.sources), structuredClone(measurements.nodes)));
  new TraceHarness({}).prepare(analysis, { callBreakdowns: ["anchor"] });
  return analysis;
}

test('views select prepared prefix work, paginate after aggregation and preserve node duration', () => {
  const { trace, measurements } = prepared();
  const analysis = analysisWithTiming(trace, measurements);
  const before = JSON.stringify(analysisSnapshot(analysis));
  const groups = buildView(analysis, { scope: "node", view: "measurements", nodeId: "anchor", limit: 1 });
  const timeline = buildView(analysis, { scope: "node", view: "timeline", nodeId: "anchor", limit: 2 });
  if (groups.view !== "measurements" || timeline.view !== "timeline") throw new Error("wrong view");
  expect(groups.node.duration_ms).toBe(10);
  expect(groups.node).not.toHaveProperty("facts");
  expect(groups.timing?.window?.elapsed_ms).toBe(30);
  expect(timeline.calls).toMatchObject({ total: 4, next_offset: 2 });
  expect(timeline.calls?.items[1]).toMatchObject({ node_id: 'flight', duration: 15, clipped: true });
  expect(groups.groups?.items[0]).toMatchObject(fixture.expected_group);
  const rest = buildView(analysis, { scope: "node", view: "timeline", nodeId: "anchor", offset: 2, limit: 2 });
  if (rest.view !== "timeline") throw new Error("wrong view");
  expect(rest.calls?.items.map(call => [call.node_id, call.duration, call.clipped])).toEqual([['anchor', 10, false], ['cutoff', 0, true]]);
  expect(rest.calls?.next_offset).toBeNull();
  expect(render(groups, { format: "text" })).toContain('30ms wall-clock');
  expect(render(timeline, { format: "text" })).toContain('next_offset=2');
  expect(JSON.stringify(analysisSnapshot(analysis))).toBe(before);
  expect(() => buildView(analysis, { scope: "node", nodeId: "unknown" })).toThrow('not found');
  expect(() => buildView(analysis, { scope: "node", view: "timeline", nodeId: "anchor", limit: 0 })).toThrow('positive integers');
});

test('not prepared, unavailable, failed, inapplicable and measured-zero remain distinguishable', () => {
  const { trace, measurements } = prepared();
  expect(buildView(new AnalysisContext(trace), { scope: "node", view: "timeline", nodeId: "anchor" })).toMatchObject({ timing: null, calls: null });
  expect(deriveCallBreakdown(trace, new Measurements(), "anchor").status).toBe("not_available");
  for (const status of ['error', 'not_applicable'] as const) {
    measurements.nodes.anchor![0]!.status = status;
    measurements.nodes.anchor![0]!.error = status === 'error' ? 'source unavailable' : null;
    const view = buildView(analysisWithTiming(trace, measurements), { scope: "node", view: "timeline", nodeId: "anchor" });
    expect(view).toMatchObject({ timing: { status, window: null }, calls: null });
  }
  measurements.nodes.anchor![0]!.status = 'measured';
  measurements.nodes.anchor![0]!.evidence = { source: 'calls', start_ms: 100, end_ms: 100 };
  const zero = buildView(analysisWithTiming(trace, new Measurements(measurements.specs, [], measurements.nodes)), { scope: "node", view: "timeline", nodeId: "anchor" });
  expect(zero).toMatchObject({ timing: { status: 'measured', window: { elapsed_ms: 0 } }, calls: { items: [], total: 0 } });
  measurements.nodes.anchor![0]!.evidence.end_ms = NaN;
  expect(() => deriveCallBreakdown(trace, measurements, 'anchor')).toThrow('Invalid measurement window');
});

test('Agent view uses saved derivation, keeps nesting and source IDs across formats and pages', () => {
  const { trace } = prepared();
  const analysis = new AnalysisContext(trace);
  expect(buildView(analysis, { scope: "trace", view: "agent" }).status).toBe('not_available');
  analysis.agentRuns = createAgentRunIR(trace.trace_id, [{ id: 'run', name: 'Agent', start_ms: 0, duration_ms: 50,
    source_node_ids: ['anchor'], items: [{ kind: 'operation', id: 'prepare', name: 'prepare', start_ms: 0, duration_ms: 40,
      operations: [{ kind: 'operation', id: 'nested', name: 'nested\nname', start_ms: 5, duration_ms: 10,
        source_node_ids: ['flight'], input: { message: 'original I/O' } }] }] }]);
  const first = buildView(analysis, { scope: "trace", view: "agent", limit: 2 });
  expect(first.rows.total).toBe(3);
  expect(first.rows.items.map(row => [row.node_id, row.depth])).toEqual([['agent-run:run', 0], ['agent-item:operation:prepare', 1]]);
  const rest = buildView(analysis, { scope: "trace", view: "agent", offset: 2 });
  expect(rest.rows.items[0]).toMatchObject({ depth: 2, parent_id: 'agent-item:operation:prepare', source_node_ids: ['flight'], span_ids: ['s-flight'], start_ms: 5 });
  expect(render(rest, { format: "text" })).toContain('nested\\nname');
  const json = JSON.parse(render(rest, { format: "json" }));
  expect(json).toEqual(JSON.parse(JSON.stringify(rest)));
  expect(render(json, { format: "html" })).toContain('data-perspective="agent"');
  analysis.agentRuns = { ...analysis.agentRuns, trace_id: 'other' };
  expect(() => buildView(analysis, { scope: "trace", view: "agent" })).toThrow('does not match');
});

test('saved analysis supports hidden-node views; bundled renderer never calculates measurements', async () => {
  const records = readFileSync(new URL('../../../../conformance/trace/fixtures/genai-basic.jsonl', import.meta.url), 'utf8').trim().split('\n').map(line => JSON.parse(line));
  const harness = new TraceHarness({ specs: genAiSpecs(), measurementFilter: () => false });
  const analysis = await harness.analyze(harness.assemble(normalizeJaegerSpans(records)));
  const node = analysis.trace.nodes.find(node => analysis.measurements.get(node.node_id, 'calls_until_node_end')?.status === 'measured')!;
  harness.prepare(analysis, { callBreakdowns: [node.node_id] });
  const loaded = loadAnalysis(analysisSnapshot(analysis));
  expect(harness.visibleMeasurements(loaded.trace, loaded.measurements).nodes).toEqual({});
  const query = buildView(loaded, { scope: "node", view: "measurements", nodeId: node.node_id });
  expect(query).toMatchObject({ timing: { status: 'measured' } });
  const published = await import('../dist/index.js');
  const serialized = JSON.parse(render(query, { format: "json" }));
  expect(published.render(serialized, { format: "text" })).toBe(render(query, { format: "text" }));
  const html = published.render(serialized, { format: "html" });
  expect(html).not.toContain('measurementWindow');
  expect(html).toContain('Call groups');
  const timeline = buildView(loaded, { scope: "node", view: "timeline", nodeId: node.node_id });
  const timelineHtml = published.render(JSON.parse(render(timeline, { format: "json" })), { format: "html" });
  expect(() => new Function(timelineHtml.match(/<script>([\s\S]*)<\/script>/)![1]!)).not.toThrow();
  expect(timelineHtml).not.toMatch(/<script[^>]+src=/);
});

test('trace and node measurements have independent owners and feed detector decisions', async () => {
  const contract = JSON.parse(readFileSync(new URL('../../../../conformance/trace/fixtures/view-ownership.json', import.meta.url), 'utf8'));
  const node = new Node({ node_id: contract.node_id, kind: 'node', name: 'intention', primary_span_id: 's', span_ids: ['s'], facts: {}, start_ms: 0, duration_ms: 10 });
  const trace = new TraceContext(contract.trace_id, new Map(), [node], new Map());
  const measurer = { spec: { kind: contract.trace_kind, scope: 'trace' as const, units: { count: 'call' }, dimensions: [], description: 'Whole trace count' },
    compute: (context: TraceContext) => [{ kind: contract.trace_kind, node_id: null, status: 'measured' as const, values: { count: context.nodes.length }, evidence: {}, error: null }] };
  const harness = new TraceHarness({ measurers: [measurer], detectors: [(n, analysis) => analysis.measurements.get(null, contract.trace_kind)?.values.count === 1
    ? [{ ref: n.node_id, source: 'policy', severity: 'info' as const, note: 'count matched chosen policy' }] : []] });
  const before = JSON.stringify(trace.nodes);
  const analysis = await harness.analyze(trace);
  expect(analysis.measurements.trace).toHaveLength(contract.expected_trace_count);
  expect(analysis.measurements.get(node.node_id, contract.node_kind)?.kind).toBe(contract.node_kind);
  expect(analysis.findings[node.node_id]?.some(f => f.source === 'policy')).toBe(true);
  const view = harness.buildView(analysis, { scope: 'trace' });
  expect(view).toMatchObject({ measurements: [{ kind: contract.trace_kind, scope: 'trace' }] });
  expect(loadAnalysis(analysisSnapshot(analysis)).measurements.trace).toEqual(analysis.measurements.trace);
  for (const format of contract.formats) expect(render(JSON.parse(render(view, { format: 'json' })), { format })).toContain(contract.trace_id);
  expect(JSON.stringify(trace.nodes)).toBe(before);
});
