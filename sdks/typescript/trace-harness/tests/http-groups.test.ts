import { archiveContents, traceTrees } from "./archive-fixture";
import { expect, test } from "bun:test";
import { readFileSync } from "node:fs";
import { NormSpan, TraceHarness, genAiSpecs } from "../src/index";
import type { DisplayNode } from "../src/view/display";

interface Fixture {
  name: string;
  diagnose?: boolean;
  spans: Array<{ span_id: string; parent_span_id: string | null; name: string; start_ms: number; dur_ms: number; service: string; attrs: Record<string, unknown> }>;
  expected: unknown;
}
interface PayloadNode {
  node_id: string;
  kind: string;
  name: string;
  primary_span_id: string;
  duration_ms: number;
  children: PayloadNode[];
}
const cases: Fixture[] = JSON.parse(readFileSync(new URL("../../../../conformance/trace/cases/http-groups.json", import.meta.url), "utf8"));
function outline(node: DisplayNode): unknown {
  const children = node.children.map(outline);
  if (node.kind) return children.length ? { node: node.node_ids[0], children } : node.node_ids[0];
  return { name: node.name, members: node.node_ids, folded: Boolean(node.folded),
    brief: Object.fromEntries(node.brief.map((field) => [field.label, field.value])), children };
}
function* walk(nodes: PayloadNode[]): Generator<PayloadNode> {
  for (const node of nodes) { yield node; yield* walk(node.children); }
}
for (const fixture of cases) {
  test(`HTTP group conformance: ${fixture.name}`, () => {
    const harness = new TraceHarness({ specs: genAiSpecs() });
    const spans = fixture.spans.map((item) => new NormSpan(item.span_id, item.parent_span_id ?? undefined, item.name, item.start_ms, item.dur_ms, item.service, false, item.attrs, { traceID: fixture.name }));
    const context = harness.assemble(new Map(spans.map((span: NormSpan) => [span.span_id, span])));
    const findings = fixture.diagnose === false ? {} : harness.diagnose(context);
    const before = JSON.stringify(context.nodes);
    expect(harness.renderDisplay(context, findings).map(outline)).toEqual(fixture.expected);
    expect(JSON.stringify(context.nodes)).toBe(before);
    const html = harness.renderInteractive(context, findings);
    const payload: { full: { roots: PayloadNode[] } } = traceTrees(html);
    const nodes = [...walk(payload.full.roots)];
    const ids = nodes.map((node) => node.node_id);
    expect(new Set(ids).size).toBe(ids.length);
    expect(new Set(nodes.filter((node) => node.kind).map((node) => node.primary_span_id))).toEqual(new Set(fixture.spans.map((span) => span.span_id)));
    if (fixture.name === "server-clock-skew") expect(nodes.find((node) => node.name.startsWith("⚠"))!.duration_ms).toBe(100);
  });
}
