import { expect, test } from "bun:test";
import { readFileSync } from "node:fs";
import { runInNewContext } from "node:vm";
import { createAgentRunIR, inspectAgentStack, inspectNodeMeasurements, renderAgentStackText, renderNodeMeasurementsText,
  Measurements, Node, TraceContext, TraceHarness, genAiSpecs, normalizeJaegerSpans, analysisSnapshot, loadAnalysis } from "../src";
import { MEASUREMENT_DIALOG_SCRIPT } from "../src/view/measurement-dialog";
import { measurementWindow, aggregateMeasurementCalls } from "../src/view/measurement-calls";
import { archiveEntries } from "./archive-fixture";

const fixture = JSON.parse(readFileSync(new URL('../../../../conformance/trace/fixtures/inspection-window.json', import.meta.url), 'utf8'));
function prepared() {
  const nodes = fixture.calls.map((call: any) => new Node({
    node_id: call.node_id, kind: call.kind, name: call.name, primary_span_id: call.span_ids[0], span_ids: call.span_ids,
    facts: {}, service: call.service, start_ms: call.start_ms, duration_ms: call.end_ms - call.start_ms,
  }));
  const trace = new TraceContext('offline', new Map(), nodes, new Map());
  const spec = { id: 'calls_until_node_end', scope: 'trace_prefix' as const, dimensions: ['kind'], units: { count: 'calls' }, description: 'prefix' };
  const result = { spec_id: spec.id, anchor_node_id: 'anchor', status: 'measured' as const,
    values: { node: { count: 4 } }, evidence: { source: 'calls', start_ms: fixture.window[0], end_ms: fixture.window[1] }, error: null };
  return { trace, measurements: new Measurements([spec], fixture.calls, { anchor: [result] }) };
}

test('offline inspection clips prefix work, paginates after aggregation and keeps node duration distinct', () => {
  const { trace, measurements } = prepared();
  const before = JSON.stringify({ nodes: trace.nodes, measurements });
  const report = inspectNodeMeasurements(trace, measurements, 'anchor', { groups: { limit: 1 }, calls: { limit: 2 } });
  expect(report.node.duration_ms).toBe(10);
  expect(report.prefix.window?.elapsed_ms).toBe(30);
  expect(report.prefix.calls).toMatchObject({ total: 4, next_offset: 2 });
  expect(report.prefix.calls?.items[1]).toMatchObject({ node_id: 'flight', duration: 15, clipped: true });
  expect(report.prefix.groups?.items[0]).toMatchObject(fixture.expected_group);
  const rest = inspectNodeMeasurements(trace, measurements, 'anchor', { calls: { offset: 2, limit: 2 } });
  expect(rest.prefix.calls?.items.map(call => [call.node_id, call.duration, call.clipped])).toEqual([['anchor', 10, false], ['cutoff', 0, true]]);
  expect(rest.prefix.calls?.next_offset).toBeNull();
  expect(renderNodeMeasurementsText(report)).toContain('30ms wall-clock');
  expect(renderNodeMeasurementsText(report)).toContain('next_offset=2');
  expect(JSON.stringify({ nodes: trace.nodes, measurements })).toBe(before);
  expect(() => inspectNodeMeasurements(trace, measurements, 'unknown')).toThrow('not found');
  expect(() => inspectNodeMeasurements(trace, measurements, 'anchor', { calls: { limit: 0 } })).toThrow('positive integers');
});

test('absent, failed, inapplicable and measured-zero windows stay distinguishable', () => {
  const { trace, measurements } = prepared();
  expect(inspectNodeMeasurements(trace, new Measurements(), 'anchor').prefix).toMatchObject({ status: 'not_available', calls: null });
  for (const status of ['error', 'not_applicable'] as const) {
    measurements.results.anchor![0]!.status = status;
    measurements.results.anchor![0]!.error = status === 'error' ? 'source unavailable' : null;
    expect(inspectNodeMeasurements(trace, measurements, 'anchor').prefix).toMatchObject({ status, window: null, groups: null, calls: null });
  }
  measurements.results.anchor![0]!.status = 'measured';
  measurements.results.anchor![0]!.evidence = { source: 'calls', start_ms: 100, end_ms: 100 };
  const zero = inspectNodeMeasurements(trace, new Measurements(measurements.specs, [], measurements.results), 'anchor');
  expect(zero.prefix).toMatchObject({ status: 'measured', window: { elapsed_ms: 0 }, calls: { items: [], total: 0 } });
  measurements.results.anchor![0]!.evidence.end_ms = NaN;
  expect(() => inspectNodeMeasurements(trace, measurements, 'anchor')).toThrow('Invalid measurement window');
});

test('Agent stack shares logical nesting, preserves source IDs and never serializes model I/O', () => {
  const { trace } = prepared();
  const ir = createAgentRunIR(trace.trace_id, [{ id: 'run', name: 'Agent', start_ms: 0, duration_ms: 50,
    source_node_ids: ['anchor'], items: [{ kind: 'operation', id: 'prepare', name: 'prepare', start_ms: 0, duration_ms: 40,
      operations: [{ kind: 'operation', id: 'nested', name: 'nested\nname', start_ms: 5, duration_ms: 10,
        source_node_ids: ['flight'], input: { toJSON() { throw new Error('must not serialize I/O'); } } }] }] }]);
  const stack = inspectAgentStack(trace, ir, { limit: 2 });
  expect(stack.rows.total).toBe(3);
  expect(stack.rows.items.map(row => [row.id, row.depth])).toEqual([['agent-run:run', 0], ['agent-item:operation:prepare', 1]]);
  const rest = inspectAgentStack(trace, ir, { offset: 2 });
  expect(rest.rows.items[0]).toMatchObject({ depth: 2, parent_id: 'agent-item:operation:prepare', source_node_ids: ['flight'], span_ids: ['s-flight'], offset_ms: 5 });
  expect(renderAgentStackText(rest)).toContain('nested\\nname');
  expect(inspectAgentStack(trace).status).toBe('not_available');
  expect(inspectAgentStack(trace, createAgentRunIR(trace.trace_id, [])).status).toBe('available');
  expect(() => inspectAgentStack(trace, { ...ir, trace_id: 'other' })).toThrow('does not match');
});

test('browser and SDK use identical window and group functions', () => {
  const browser = runInNewContext(MEASUREMENT_DIALOG_SCRIPT + '\n({measurementWindow, aggregateMeasurementCalls})');
  const calls = fixture.calls.map((call: any) => ({ ...call, key: `${call.kind}:${call.id}`, target: '' }));
  const server = measurementWindow(calls, ...fixture.window as [number, number]);
  const client = browser.measurementWindow(calls, ...fixture.window);
  expect(JSON.stringify(client)).toBe(JSON.stringify(server));
  expect(JSON.stringify(browser.aggregateMeasurementCalls(client))).toBe(JSON.stringify(aggregateMeasurementCalls(server)));
});

test('saved analysis supports hidden-node queries and matches the bundled HTML projection', async () => {
  const records = readFileSync(new URL('../../../../conformance/trace/fixtures/genai-basic.jsonl', import.meta.url), 'utf8').trim().split('\n').map(line => JSON.parse(line));
  const harness = new TraceHarness({ specs: genAiSpecs(), measurementFilter: () => false });
  const loaded = loadAnalysis(analysisSnapshot(await harness.analyze(harness.assemble(normalizeJaegerSpans(records)))));
  const node = loaded.trace.nodes.find(node => loaded.measurements.get(node.node_id, 'calls_until_node_end')?.status === 'measured')!;
  expect(harness.visibleMeasurements(loaded.trace, loaded.measurements).results).toEqual({});
  const query = inspectNodeMeasurements(loaded.trace, loaded.measurements, node.node_id);
  expect(query.prefix.status).toBe('measured');
  // Test emitted package too: browser source must survive bundling, not only source execution.
  const published = await import('../dist/index.js');
  const html = published.renderInteractive(loaded.trace, {}, { measurements: loaded.measurements });
  const archive = archiveEntries(html);
  const calls = JSON.parse(new TextDecoder().decode(archive['measurement-calls.json']));
  const script = html.match(/<script>([\s\S]*)<\/script>/)![1]!;
  const pure = script.slice(script.indexOf('const measurementWindow ='), script.indexOf('let measurementState;'));
  const browser = runInNewContext(pure + '\n({measurementWindow, aggregateMeasurementCalls})');
  const window = query.prefix.window!;
  expect(JSON.stringify(browser.aggregateMeasurementCalls(browser.measurementWindow(calls, window.start_ms, window.end_ms))))
    .toBe(JSON.stringify(query.prefix.groups!.items));
  expect(published.renderNodeMeasurementsText(published.inspectNodeMeasurements(loaded.trace, loaded.measurements, node.node_id)))
    .toBe(renderNodeMeasurementsText(query));
});
