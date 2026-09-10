import { archiveContents, traceTrees } from "./archive-fixture";
import { expect, test } from "bun:test";
import { readFileSync } from "node:fs";
import { NormSpan, TraceHarness, genAiSpecs } from "../src/index";

interface Case {
  name: string;
  spans: Array<{ span_id: string; parent_span_id: string | null; name: string; start_ms: number; dur_ms: number; service: string; attrs: Record<string, unknown> }>;
  expected: Array<{ source: string; severity: string; span_ids: string[]; [key: string]: unknown }>;
  limits?: Record<string, { count: number; total: number }>;
}
const cases: Case[] = JSON.parse(readFileSync(new URL("../../../../conformance/trace/cases/http-detectors.json", import.meta.url), "utf8"));
for (const fixture of cases) {
  test(`HTTP detector conformance: ${fixture.name}`, async () => {
    const harness = new TraceHarness({ specs: genAiSpecs() });
    const spans = fixture.spans.map((item) => new NormSpan(item.span_id, item.parent_span_id ?? undefined, item.name, item.start_ms, item.dur_ms, item.service, false, item.attrs, { traceID: fixture.name }));
    const context = harness.assemble(new Map(spans.map((span) => [span.span_id, span])));
    const findings = Object.values(await harness.diagnose(context)).flat().filter((finding) => finding.source.startsWith("http_"));
    if (fixture.limits) {
      for (const [source, limit] of Object.entries(fixture.limits)) {
        const hits = findings.filter((finding) => finding.source === source);
        expect(hits).toHaveLength(limit.count);
        expect(hits[0]!.note).toContain(`共 ${limit.total} 条`);
      }
      return;
    }
    expect(findings).toHaveLength(fixture.expected.length);
    for (const expected of fixture.expected) {
      const hits = findings.filter((finding) => finding.source === expected.source
        && JSON.stringify(finding.data?.span_ids) === JSON.stringify(expected.span_ids));
      expect(hits).toHaveLength(1);
      const hit = hits[0]!;
      const actual: Record<string, unknown> = { source: hit.source, severity: hit.severity, ...hit.data };
      expect(Object.fromEntries(Object.keys(expected).map((key) => [key, actual[key]]))).toEqual(expected);
      expect(context.view().by_id.has(hit.ref)).toBe(true);
      if (hit.source === "http_serial_same_api") {
        expect(Number(hit.data!.http_total_ms) + Number(hit.data!.gap_ms)).toBe(hit.data!.wall_ms);
      }
      expect(hit.note).not.toContain("secret");
    }
    const html = harness.renderInteractive(context, await harness.diagnose(context));
    for (const finding of findings) expect(html + archiveContents(html)).toContain(finding.source);
  });
}
