import { expect, test } from "bun:test";
import { readFileSync } from "node:fs";
import type { SessionView, Node as ObservationNode, Input, ModelCall, ToolCall, Operation, Turn } from "@compforge/agent-observation";
import { AnalysisContext, TraceHarness, analysisSnapshot, loadAnalysis, buildView, genAiSpecs, normalizeJaegerSpans } from "../src";
import { validateSessionViews, walkNodes } from "../src/observation";
import { sessionRoots } from "../src/view/observation";
import { reportHtml } from "./view-fixture";

const fixture: SessionView[] = JSON.parse(readFileSync(new URL("../../../../conformance/trace/cases/genai-basic.session-views.json", import.meta.url), "utf8"));
const raw = readFileSync(new URL("../../../../conformance/trace/fixtures/genai-basic.jsonl", import.meta.url), "utf8").trim().split("\n").map(line => JSON.parse(line));
function harness() { return new TraceHarness({ specs: genAiSpecs(), sessionViewExtractor: {extract: () => structuredClone(fixture)} }); }
function context() { return harness().assemble(normalizeJaegerSpans(raw)); }
function view(id: string, ...nodes: (Input | ModelCall | ToolCall | Operation | Turn)[]): SessionView {return {schema_version:"agent-ledger/session@1", session:{source:"fixture",id},nodes};}
function rows(roots: Array<Record<string, unknown>>): Array<Record<string, any>> {return roots.flatMap(root => [root, ...rows(root.children as Array<Record<string, unknown>>)]);}

test("extractor, snapshot and renderer share Observation directly", () => {
  const h = harness(), ctx = context();
  const extracted = h.extractSessionViews(ctx)!;
  expect(extracted).toEqual(fixture);
  const analysis = h.prepare(new AnalysisContext(ctx), {sessionViews:true});
  expect(analysis.sessionViews).toEqual(fixture);
  const snapshot = analysisSnapshot(analysis);
  const loaded = loadAnalysis(JSON.parse(JSON.stringify(snapshot)));
  expect(analysisSnapshot(loaded)).toEqual(snapshot);
  const copy = validateSessionViews(extracted,ctx);copy[0]!.nodes.length=0;
  expect(extracted[0]!.nodes.length).toBeGreaterThan(0);
  expect(reportHtml(h,ctx)).toContain('data-perspective="agent"');
  expect(reportHtml(new TraceHarness({}),ctx)).not.toContain('data-perspective="agent"');
});

test("ToolCall expands foreign Session at its call site without merging models", () => {
  const before = JSON.stringify(fixture);
  const roots = sessionRoots(context(), fixture, {});
  const tool = rows([roots[0]!]).find(row => row.kind === "tool_call")!;
  expect(tool.children[0]).toMatchObject({kind:"session",name:fixture[1]!.session.id});
  expect(rows(tool.children).some(row => row.name === "worker-model")).toBe(true);
  expect([...walkNodes(fixture[0]!.nodes)].filter(item=>item.kind==="model_call")).toHaveLength(2);
  expect(new Set(rows(roots).map(row=>row.node_id)).size).toBe(rows(roots).length);
  expect(JSON.stringify(fixture)).toBe(before);
});

test("cyclic and unavailable Sessions remain references, repeated call sites have unique display IDs", () => {
  const a = view("a", {kind:"tool_call",id:"first",sessions:[{source:"fixture",id:"b"},{source:"fixture",id:"b"}]}, {kind:"tool_call",id:"second",sessions:[{source:"fixture",id:"b"},{source:"remote",id:"absent"}]});
  const b = view("b",{kind:"tool_call",id:"back",sessions:[a.session]});
  const result=rows(sessionRoots(context(),[a,b],{}));
  expect(result.length).toBeLessThan(30);
  expect(result.some(row=>row.details.sessions?.includes('"cycle"'))).toBe(true);
  expect(result.some(row=>row.details.sessions?.includes('"unavailable"'))).toBe(true);
  expect(new Set(result.map(row=>row.node_id)).size).toBe(result.length);
});

test("all item kinds, missing timing, tool names and explicit null remain observable", () => {
  const native=view("session",{kind:"input",id:"input",content:null},{kind:"turn",id:"turn",nodes:[{kind:"tool_call",id:"read",request:{tool_name:"read_file",input:{path:"/workspace/market.md"}},response:{output:null}},{kind:"model_call",id:"model",response:{output:"partial"}}]},{kind:"operation",id:"op",operation:"compact",data:null});
  const result=rows(sessionRoots(context(),[native],{}));
  const tool=result.find(row=>row.kind==="tool_call")!;
  expect(tool.name_variants).toEqual(["read_file · market.md","market.md"]);
  expect(JSON.parse(tool.details.response)).toEqual({output:null});
  expect(result.every(row=>row.timing_known===false)).toBe(true);
  expect(result.find(row=>row.kind==="input")!.details.content).toBe("null");
  const analysis=new AnalysisContext(context());analysis.sessionViews=[native];
  expect(loadAnalysis(analysisSnapshot(analysis)).sessionViews).toEqual([native]);
});

test("local evidence is validated; remote evidence is not bound to same-ID local nodes", () => {
  const bad=view("session",{kind:"operation",operation:"bad",id:"bad",evidence:[{source:"trace-node",id:"missing"}]});
  expect(()=>validateSessionViews([bad],context())).toThrow("unknown node IDs");
  bad.nodes[0]!.evidence![0]!.uri="trace:other";
  expect(validateSessionViews([bad],context())).toEqual([bad]);
  expect(rows(sessionRoots(context(),[bad],{}))[1]!.source_node_ids).toEqual([]);
  const other=structuredClone(bad);other.session.source="other";
  expect(validateSessionViews([bad,other],context())).toHaveLength(2);
  expect(()=>validateSessionViews([bad,bad],context())).toThrow("duplicate Session");
});

test("Observation permits async children and rejects dangling model-call associations", () => {
  const native=view("session",{kind:"operation",operation:"dispatch",id:"parent",started_at:"2026-10-25T00:00:01Z",finished_at:"2026-10-25T00:00:02Z",nodes:[{kind:"operation",operation:"async",id:"child",started_at:"2026-10-25T00:00:03Z"}]});
  expect(validateSessionViews([native],context())).toEqual([native]);
  native.nodes.push({kind:"tool_call",id:"bad",model_call_id:"missing"});
  expect(()=>validateSessionViews([native],context())).toThrow("missing model call");
});

test("unprepared and prepared-empty observations stay distinct", () => {
  const analysis=new AnalysisContext(context());
  expect(buildView(analysis,{scope:"trace",view:"agent"}).status).toBe("not_available");
  analysis.sessionViews=[];
  const loaded=loadAnalysis(analysisSnapshot(analysis));
  expect(loaded.sessionViews).toEqual([]);
  expect(buildView(loaded,{scope:"trace",view:"agent"}).status).toBe("available");
});
