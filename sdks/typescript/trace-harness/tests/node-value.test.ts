import { expect, test } from "bun:test";
import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import { assemble, genAiSpecs, loadAnalysis, NormSpan, SpecSet, type AnalysisSnapshot } from "../src";

function fixture(name: string) {
  return JSON.parse(readFileSync(resolve(import.meta.dir, "../../../../conformance/trace/cases", name), "utf8"));
}
function span(id: string, attrs: Record<string, unknown>, parent?: string) {
  return new NormSpan(id, parent, "http.server", 0, 1, "sandbox-server", false, attrs, {});
}

test("HTTP search value follows shared URL precedence, even before view preparation", () => {
  for (const entry of fixture("node-value.json")) {
    const primary = span("root", { "http.request.method": "POST", ...entry.attrs });
    for (const prepare of [true, false]) {
      const context = assemble(new Map([["root", primary]]), genAiSpecs(), [], prepare);
      expect(context.nodes[0]!.kind).toBe("http");
      expect(context.nodes[0]!.value).toBe(entry.value);
    }
  }
});

test("business projection receives claimed spans without concatenating child content", () => {
  const specs = new SpecSet([
    { kind: "business", matches: s => s.span_id === "root", claims: () => new Set(["sat"]),
      value: (p, satellites) => `${p.name}:${satellites[0]!.attrs.text}` },
    { kind: "child", matches: s => s.span_id === "child" },
  ]);
  const spans = [span("root", {}), span("sat", { text: "selected" }, "root"), span("child", { text: "not selected" }, "root")];
  const context = assemble(new Map(spans.map(s => [s.span_id, s])), specs);
  expect(Object.fromEntries(context.nodes.map(n => [n.node_id, n.value])))
    .toEqual({ root: "http.server:selected", child: "" });
});

test("offline analysis preserves value and accepts older snapshots", () => {
  const snapshot: AnalysisSnapshot = fixture("genai-basic.analysis.json");
  expect(loadAnalysis(snapshot).trace.nodes.map(n => n.value)).toEqual(snapshot.nodes.map(n => n.value));
  for (const node of snapshot.nodes) delete node.value;
  expect(loadAnalysis(snapshot).trace.nodes.every(n => n.value === "")).toBe(true);
});
