import { expect, test } from "bun:test";
import { runInNewContext } from "node:vm";
import { readFileSync } from "node:fs";
import { MEASUREMENT_DIALOG_SCRIPT, measurementCallDetails } from "../src/view/measurement-dialog";
import { TraceHarness, genAiSpecs, normalizeJaegerSpans, analysisSnapshot, loadAnalysis } from "../src";
import { archiveEntries } from "./archive-fixture";

const browser = runInNewContext(MEASUREMENT_DIALOG_SCRIPT + '\n({measurementWindow, aggregateMeasurementCalls, measurementTimelineRows})');
const source = (key: string, start_ms: number, end_ms: number, extra = {}) => ({ key, id: key, node_id: key, kind: 'http', service: 'catalog', name: 'GET /items', start_ms, end_ms, ...extra });

test("window clips in-flight calls, keeps calls at cutoff and excludes future work", () => {
  const calls = browser.measurementWindow([source('a', 0, 20), source('b', 15, 40), source('c', 30, 35), source('d', 31, 50)], 0, 30);
  expect(calls.map((call: any) => [call.key, call.duration, call.clipped])).toEqual([['a', 20, false], ['b', 15, true], ['c', 0, true]]);
  const groups = browser.aggregateMeasurementCalls(calls);
  expect(groups[0]).toMatchObject({ count: 3, total: 35, max: 20 });
});

test("aggregation preserves service, operation and kind boundaries without collapsing parallel work", () => {
  const calls = browser.measurementWindow([source('a', 0, 20), source('b', 0, 20), source('c', 0, 10, { service: 'other' }), source('d', 0, 5, { name: 'POST /items' }), source('e', 0, 2, { kind: 'service-api' })], 0, 20);
  const groups = browser.aggregateMeasurementCalls(calls);
  expect(groups.length).toBe(4);
  expect(groups[0]).toMatchObject({ count: 2, total: 40, max: 20 });
});

test("timeline orders siblings chronologically and supports collapse and group context", () => {
  const calls = browser.measurementWindow([source('root', 0, 50, { name: 'root' }), source('late', 30, 40, { parent: 'root' }), source('early', 5, 10, { parent: 'root' }), source('other', 1, 3, { parent: 'root', name: 'other' })], 0, 50);
  expect(browser.measurementTimelineRows(calls, new Set(), null).map((call: any) => call.key)).toEqual(['root', 'other', 'early', 'late']);
  expect(browser.measurementTimelineRows(calls, new Set(['root']), null).map((call: any) => call.key)).toEqual(['root']);
  expect(browser.measurementTimelineRows(calls, new Set(['root']), calls[1].group).map((call: any) => [call.key, call.depth])).toEqual([['root', 0], ['early', 1], ['late', 1]]);
});

function fixture() {
  const records = readFileSync(new URL('../../../../conformance/trace/fixtures/genai-basic.jsonl', import.meta.url), 'utf8').trim().split('\n').map(line => JSON.parse(line));
  const harness = new TraceHarness({ specs: genAiSpecs(), measurementFilter: (node, measurement) => node.parent_node_id === undefined && measurement.spec_id === 'calls_until_node_end' });
  return { harness, trace: harness.assemble(normalizeJaegerSpans(records)) };
}

test("offline dialog keeps one shared call index and the selected anchor; row details omit measurements", async () => {
  const { harness, trace } = fixture();
  const analysis = await harness.analyze(trace);
  const before = JSON.stringify(analysis.measurements);
  const html = harness.renderInteractive(trace, analysis.findings, { measurements: analysis.measurements });
  const archive = archiveEntries(html);
  expect(JSON.parse(new TextDecoder().decode(archive['measurement-calls.json']))).toHaveLength(analysis.measurements.sources.length);
  expect(html).toContain('openMeasurements(n)');
  const markerSource = html.match(/function appendMeasurementMarker[\s\S]*?(?=\nfunction renderInto)/)![0];
  const marker = runInNewContext(markerSource + `;let result;appendMeasurementMarker({appendChild: value => result = value}, {has_measurements: true});result`, { document: { createElement: () => ({ setAttribute() {} }) } });
  expect(marker.innerHTML).toContain('<span>耗时分析</span>');
  expect(marker.innerHTML).toContain('fill="#facc15"');
  expect(html).toContain("marker.setAttribute('aria-haspopup','dialog')");
  expect(html).not.toContain('+findings(n)+measurementBlock(n)');
  expect(html).toContain('role="tablist"');
  expect(html).toContain('调用聚合');
  expect(html).toContain('调用时间线');
  expect(html).not.toMatch(/<script[^>]+src=/);
  expect(() => new Function(html.match(/<script>([\s\S]*)<\/script>/)![1]!)).not.toThrow();
  expect(JSON.stringify(analysis.measurements)).toBe(before);
});

test("call labels remain available from saved analysis without raw spans", async () => {
  const { harness, trace } = fixture();
  const loaded = loadAnalysis(analysisSnapshot(await harness.analyze(trace)));
  const calls = measurementCallDetails(loaded.trace, loaded.measurements);
  expect(calls.length).toBe(loaded.measurements.sources.length);
  expect(calls.every(call => call.name && call.node_id)).toBe(true);
  expect(calls.every(call => call.parent !== call.key)).toBe(true);
});

test("HTTP operation labels use protocol endpoints rather than merging generic POST spans", () => {
  const { harness, trace } = fixture();
  const measurements = harness.measure(trace);
  const call = measurements.sources.find(item => item.kind === 'http')!;
  const span = trace.spans.get(call.span_ids[0]!)!;
  span.attrs['url.full'] = 'https://api.example.test/v1/items?token=secret';
  span.attrs['http.route'] = '/v1/items';
  const detail = measurementCallDetails(trace, measurements).find(item => item.key === `http:${call.id}`)!;
  expect(detail.name).toContain('/v1/items');
  expect(detail.target).toBe('api.example.test');
  expect(detail.name).not.toContain('secret');
});
