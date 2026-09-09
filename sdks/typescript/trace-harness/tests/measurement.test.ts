import { expect, test } from "bun:test";
import { readFileSync } from "node:fs";
import { type FactTransform, TransformContext, buildView, Node, NormSpan, TraceContext, TraceHarness, genAiSpecs, analysisSnapshot, loadAnalysis, type Measurer, type MeasurementSpec, mergeTraceContributions, measurementsMd } from "../src/index";

import { prefixValues } from "../src/analyze/measure";
import type { CallSource } from "../src/model/measurement";

interface Case {
  name: string;
  presentation?: { anchor_span_ids: string[]; spec_ids: string[] };
  spans: Array<{ span_id: string; parent_span_id: string | null; name: string; start_ms: number; dur_ms: number; service: string; attrs: Record<string, unknown> }>;
  expected: Record<string, Record<string, { count: number; duration_sum_ms: number; covered_ms: number }>>;
}
const cases: Case[] = JSON.parse(readFileSync(new URL("../../../../conformance/trace/cases/measurements.json", import.meta.url), "utf8"));
function build(fixture: Case, harness = new TraceHarness({ specs: genAiSpecs() })): TraceContext {
  return harness.assemble(new Map(fixture.spans.map((item) => [item.span_id, new NormSpan(item.span_id, item.parent_span_id ?? undefined, item.name, item.start_ms, item.dur_ms, item.service, false, item.attrs, { traceID: fixture.name })])));
}
for (const fixture of cases) test(`Measurement conformance: ${fixture.name}`, () => {
  const harness = new TraceHarness({ specs: genAiSpecs() });
  const trace = build(fixture, harness);
  const analysis = harness.analyze(trace, false);
  expect(analysis.findings).toEqual({});
  for (const [id, expected] of Object.entries(fixture.expected)) {
    const node = trace.view().by_span.get(id)!;
    const result = analysis.measurements.get(node.node_id, "calls_until_node_end")!;
    expect(result.status).toBe("measured");
    expect(result.values).toEqual(expected);
    expect(result.evidence.end_ms).toBe(node.end_ms);
  }
  expect(trace.nodes.every((node) => !Object.hasOwn(node.facts, "self_ms"))).toBe(true);
});
const node = () => new Node({ kind: "tool", name: "n", node_id: "n", primary_span_id: "n", span_ids: ["n"], facts: {}, start_ms: 0, duration_ms: 10 });
test("fact dependencies memoize whole producer outputs", () => {
  const n = node(); let calls = 0;
  new TransformContext(buildView([n]), [{ produces: ["c"], applies: () => true, compute: (n, ctx) => ({ c: Number(ctx.get(n, "a")) + Number(ctx.get(n, "b")) }) }, { produces: ["a", "b"], applies: () => true, compute: () => { calls++; return { a: 2, b: 3 }; } }]).materialize([[n, "c"]]);
  expect(n.facts).toEqual({ a: 2, b: 3, c: 5 }); expect(calls).toBe(1);
});
for (const failure of ["duplicate", "base", "cycle", "undeclared"]) test(`invalid transform: ${failure}`, () => {
  const n = node(); const producer: FactTransform = { produces: ["a"], applies: () => true, compute: () => ({ a: 1 }) };
  let items = [producer];
  if (failure === "duplicate") items.push(producer);
  else if (failure === "base") n.facts.a = 9;
  else if (failure === "cycle") items = [{ produces: ["a"], applies: () => true, compute: (n, ctx) => ({ a: ctx.get(n, "b") }) }, { produces: ["b"], applies: () => true, compute: (n, ctx) => ({ b: ctx.get(n, "a") }) }];
  else items = [{ ...producer, compute: () => ({ other: 1 }) }];
  const before = { ...n.facts };
  expect(() => new TransformContext(buildView([n]), items).materialize([[n, "a"]])).toThrow(); expect(n.facts).toEqual(before);
});
test("curl is an ordinary fact, materialized only on request", () => {
  let calls = 0;
  const transform: FactTransform = { produces: ["curl"], applies: () => true, compute: (node, ctx) => { calls++; return { curl: `curl -X ${ctx.get(node, "method") ?? "GET"} example.test` }; } };
  const harness = new TraceHarness({ specs: genAiSpecs(), transforms: [transform] });
  const trace = build(cases[0]!, harness);
  const html = harness.renderInteractive(trace);
  expect(calls).toBe(0); expect(html).not.toContain("curl -X");
  harness.transformAll(trace, "curl");
  expect(calls).toBe(trace.nodes.length);
  expect(harness.renderInteractive(trace)).toContain("curl -X");
  expect(trace.nodes.every((node) => Object.hasOwn(node.facts, "curl"))).toBe(true);
  harness.transformAll(trace, "curl");
  expect(calls).toBe(trace.nodes.length);
  const loaded = loadAnalysis(JSON.parse(JSON.stringify(analysisSnapshot(harness.analyze(trace, false)))));
  expect(harness.renderInteractive(loaded.trace)).toContain("curl -X");
});
test("measurers run once, detectors consume measurements and previous findings", () => {
  let calls = 0; const seen: boolean[] = [];
  const custom: Measurer = { spec: { id: "custom", scope: "node", units: { size: "item" }, description: "Size", dimensions: [] }, compute: (trace) => {
    calls++; return trace.nodes.map((node) => ({ spec_id: "custom", anchor_node_id: node.node_id, status: "measured", values: { size: 7 }, evidence: {}, error: null }));
  } };
  const harness = new TraceHarness({ specs: genAiSpecs(), measurers: [custom], detectors: [(node, analysis) => {
    expect(analysis.measurements.get(node.node_id, "custom")!.values).toEqual({ size: 7 });
    return [{ ref: node.node_id, source: "custom", severity: "info" }];
  }, (node, analysis) => { seen.push(analysis.findings[node.node_id]!.some((f) => f.source === "custom")); return []; }] });
  const trace = build(cases[0]!, harness); const a = harness.analyze(trace); const b = harness.analyze(trace, false);
  expect(calls).toBe(2); expect(seen.every(Boolean)).toBe(true); expect(b.findings).toEqual({}); expect(a.measurements).not.toBe(b.measurements);
});
test("errors and not applicable never become zero", () => {
  const spec: MeasurementSpec = { id: "broken", scope: "node", units: {}, description: "Failure", dimensions: [] };
  const harness = new TraceHarness({ specs: genAiSpecs(), measurers: [{ spec, compute: () => { throw new Error("missing input"); } }, { spec: { ...spec, id: "skip" }, compute: () => [] }] });
  const trace = build(cases[0]!, harness), result = harness.measure(trace);
  for (const node of trace.nodes) { expect(result.get(node.node_id, "broken")!.status).toBe("error"); expect(result.get(node.node_id, "broken")!.values).toEqual({}); expect(result.get(node.node_id, "skip")!.status).toBe("not_applicable"); }
});
test("saved analysis renders offline without recomputation", () => {
  const harness = new TraceHarness({ specs: genAiSpecs() }); const trace = build(cases[1]!);
  const snapshot = analysisSnapshot(harness.analyze(trace, false)); const loaded = loadAnalysis(JSON.parse(JSON.stringify(snapshot)));
  expect(analysisSnapshot(loaded)).toEqual(snapshot); expect(loaded.trace.spans.size).toBe(0); expect(loaded.trace.span_count).toBe(trace.span_count);
  expect(harness.renderInteractive(loaded.trace, {}, { measurements: loaded.measurements })).toContain('"duration_sum_ms":50');
});
test("prefix sweep matches an independent naive interval oracle", () => {
  const sources: CallSource[] = Array.from({ length: 100 }, (_, i) => ({ id: String(i), kind: "http", node_id: String(i), span_ids: [String(i)], start_ms: i % 37, end_ms: i % 37 + i % 19 }));
  const values = prefixValues(sources, Array.from({ length: 80 }, (_, i) => i), 0);
  for (let cut = 0; cut < 80; cut++) {
    const started = sources.filter((source) => source.start_ms <= cut);
    const total = started.reduce((sum, source) => sum + Math.max(0, Math.min(source.end_ms, cut) - source.start_ms), 0);
    let covered = 0; for (let t = 0; t < cut; t++) if (sources.some((source) => source.start_ms <= t && source.end_ms > t)) covered++;
    expect(values.get(cut)!.http).toEqual({ count: started.length, duration_sum_ms: total, covered_ms: covered });
  }
});

test("self measurement clips children and includes leaf duration", () => {
  const parent = new Node({ kind: "tool", name: "p", node_id: "p", primary_span_id: "p", span_ids: ["p"], facts: {}, start_ms: 0, duration_ms: 10 });
  const child = new Node({ kind: "tool", name: "c", node_id: "c", primary_span_id: "c", span_ids: ["c"], facts: {}, start_ms: -10, duration_ms: 15, parent_node_id: "p" });
  const trace = new TraceContext("t", new Map(), [parent, child], new Map());
  const result = new TraceHarness({}).measure(trace);
  expect(result.get("p", "self_ms")!.values).toEqual({ self_ms: 5 });
  expect(result.get("c", "self_ms")!.values).toEqual({ self_ms: 15 });
});

test("invalid intervals are errors rather than zero", () => {
  const bad = new Node({ kind: "tool", name: "bad", node_id: "bad", primary_span_id: "bad", span_ids: ["bad"], facts: {}, start_ms: 0, duration_ms: -1 });
  const result = new TraceHarness({}).measure(new TraceContext("bad", new Map(), [bad], new Map()));
  expect(result.results.bad!.every((value) => value.status === "error" && Object.keys(value.values).length === 0)).toBe(true);
});
test("failed measurer results cannot carry numeric values", () => {
  const spec: MeasurementSpec = { id: "invalid", scope: "node", units: { n: "item" }, description: "Invalid output", dimensions: [] };
  const harness = new TraceHarness({ specs: genAiSpecs(), measurers: [{ spec, compute: (trace) => trace.nodes.map((node) => ({ spec_id: spec.id, anchor_node_id: node.node_id, status: "error", values: { n: 0 }, evidence: {}, error: "missing input" })) }] });
  const trace = build(cases[0]!, harness), result = harness.measure(trace);
  expect(trace.nodes.every((node) => Object.keys(result.get(node.node_id, "invalid")!.values).length === 0)).toBe(true);
});

test("failed transform batch retries without cached partial dependencies", () => {
  const n = node(); let calls = 0;
  const context = new TransformContext(buildView([n]), [
    { produces: ["a"], applies: () => true, compute: () => { calls++; return { a: 1 }; } },
    { produces: ["b"], applies: () => true, compute: (n, ctx) => { const a = Number(ctx.get(n, "a")); if (calls === 1) throw new Error("temporary"); return { b: a + 1 }; } },
  ]);
  expect(() => context.materialize([[n, "b"]])).toThrow("temporary");
  expect(n.facts).toEqual({});
  context.materialize([[n, "b"]]);
  expect(calls).toBe(2); expect(n.facts).toEqual({ a: 1, b: 2 });
});
test("missing outputs cache once and unknown nodes are rejected", () => {
  const n = node(); let calls = 0;
  const ctx = new TransformContext(buildView([n]), [{ produces: ["optional"], applies: () => true, compute: () => { calls++; return {}; } }]);
  ctx.materialize([[n, "optional"]]); ctx.materialize([[n, "optional"]]);
  expect(calls).toBe(1); expect(n.facts).toEqual({});
  expect(() => ctx.materialize([[node(), "optional"]])).toThrow("does not belong");
});
test("projection requests its facts without computing unrelated transforms", () => {
  let calls = 0;
  const harness = new TraceHarness({
    specs: [{ kind: "test", matches: () => true, build: () => ({ method: "POST" }), project_requires: ["label"], project: (node) => [{ label: "label", value: String(node.facts.label) }] }],
    transforms: [
      { produces: ["label"], applies: () => true, compute: (node, ctx) => ({ label: ctx.get(node, "method") }) },
      { produces: ["curl"], applies: () => true, compute: (node, ctx) => { calls++; return { curl: `curl -X ${ctx.get(node, "method")} example.test` }; } },
      { produces: ["repro"], applies: () => true, compute: (node, ctx) => ({ repro: { command: ctx.get(node, "curl") } }) },
    ],
  });
  const span = new NormSpan("n", undefined, "request", 0, 10, undefined, false, {}, { traceID: "t" });
  const trace = harness.assemble(new Map([["n", span]]));
  expect(trace.nodes[0]!.brief[0]!.value).toBe("POST"); expect(calls).toBe(0);
  harness.analyze(trace); harness.renderInteractive(trace); expect(calls).toBe(0);
  expect(harness.transform(trace.nodes[0]!, trace, "repro")).toEqual({ repro: { command: "curl -X POST example.test" } });
  harness.transformAll(trace, "curl", "repro"); expect(calls).toBe(1);
  const other = harness.assemble(new Map([["n", span]]));
  expect(other.nodes[0]!.facts.curl).toBeUndefined();
  harness.transform(other.nodes[0]!, other, "curl"); expect(calls).toBe(2);
});


test("report selection preserves analysis and offline evidence", () => {
  const selection = cases[0]!.presentation!;
  const seen: boolean[] = [];
  const harness = new TraceHarness(mergeTraceContributions({
    specs: genAiSpecs(),
    measurementFilter: (node, measurement, trace) => {
      expect(trace.view().by_id.get(node.node_id)).toBe(node);
      return selection.anchor_span_ids.includes(node.primary_span_id) && selection.spec_ids.includes(measurement.spec_id);
    },
    detectors: [(node, analysis) => { seen.push(!!analysis.measurements.get(node.node_id, "self_ms")); return []; }],
  }, { measurementFilter: () => false }));
  const trace = build(cases[0]!, harness);
  const analysis = harness.analyze(trace);
  expect(seen.length).toBe(trace.nodes.length); expect(seen.every(Boolean)).toBe(true);
  const before = JSON.stringify(analysisSnapshot(analysis));
  const visible = harness.visibleMeasurements(trace, analysis.measurements);
  expect(Object.keys(visible.results)).toEqual([trace.view().by_span.get("b")!.node_id]);
  expect(Object.values(visible.results).flat().map((m) => m.spec_id)).toEqual(["calls_until_node_end"]);
  expect(visible.sources).toBe(analysis.measurements.sources);
  const md = measurementsMd(trace, visible);
  expect(md).toContain("| b | calls_until_node_end |"); expect(md).not.toContain("| a | calls_until_node_end |");
  expect(md).not.toContain("self_ms");
  const loaded = loadAnalysis(JSON.parse(before));
  const html = harness.renderInteractive(loaded.trace, {}, { measurements: loaded.measurements });
  expect(html).toContain('"duration_sum_ms":30'); expect(html).not.toContain('"id":"self_ms"');
  expect(JSON.stringify(analysisSnapshot(analysis))).toBe(before);
  expect(analysisSnapshot(loaded)).toEqual(analysisSnapshot(analysis));
  const hidden = new TraceHarness({ measurementFilter: () => false });
  expect(hidden.visibleMeasurements(trace, analysis.measurements).results).toEqual({});
  expect(measurementsMd(trace, hidden.visibleMeasurements(trace, analysis.measurements))).toBe("");
  expect(hidden.renderInteractive(trace, {}, { measurements: analysis.measurements })).toContain('"measurements":[]');
});
