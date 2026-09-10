import { expect, test } from "bun:test";
import { mkdtemp, readFile, writeFile, rm, readdir } from "node:fs/promises";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { fileURLToPath } from "node:url";
import { JaegerFileSource, TraceHarness, genAiSpecs, normalizeJaegerSpans, analysisSnapshot } from "../src/index";
import { fixture, harness } from "./loading-fixture";

test("file source streams JSONL, filters unique traces, projects fields and checks storage identity", async () => {
  const path = await mkdtemp(join(tmpdir(), "jaeger-test-"));
  try {
    const docs = [fixture.docs[0], { ...fixture.docs[0], traceID: "t1", startTime: 9000 },
      { ...fixture.docs[0], traceID: "t1", spanID: "child", startTime: 10000, references: [{ refType: "CHILD_OF", spanID: "s" }] }];
    const file = join(path, "input.jsonl"); await writeFile(file, docs.map(doc => JSON.stringify(doc)).join("\r\n"));
    const source = new JaegerFileSource(file, { indexDir: join(path, "index") });
    try {
      expect(await Array.fromAsync(source.select({ limit: 1, order: "latest" }))).toEqual(["t1"]);
      expect(await Array.fromAsync(source.select({ attr_eq: { payload: "hello" }, until_ms: 8 }))).toEqual(["t0"]);
      expect(await Array.fromAsync(source.select({ trace_ids: ["t1"], operation_names: ["missing"] }))).toEqual([]);
      const spans = await source.fetch("t1", ["gen_ai.operation.name"]);
      expect(spans.size).toBe(2); expect(spans.get("child")!.parent_span_id).toBe("s");
      const span = spans.get("s")!; expect(span.fieldState("payload")).toBe("unloaded");
      const ref = { trace_id: "t1", span_id: "s", index: span.storage_index, document_id: span.storage_id };
      expect((await source.read([ref], ["payload"])).get("s")!.attrs.payload).toBe("hello");
      expect((await source.read([{ ...ref, document_id: "different" }], null)).size).toBe(0);
    } finally { await source.close(); }
    const again = new JaegerFileSource(file, { indexDir: join(path, "index") });
    try { expect((await again.fetch("t1", [])).size).toBe(2); } finally { await again.close(); }
  } finally { await rm(path, { recursive: true, force: true }); }
});

test("UI JSON resolves processes and full preparation renders complete evidence", async () => {
  const path = await mkdtemp(join(tmpdir(), "jaeger-ui-test-"));
  try {
    const file = join(path, "input.json");
    await writeFile(file, JSON.stringify({ data: [{ traceID: "t0", processes: { p: { serviceName: "chat-server" } }, spans: [{ ...fixture.docs[0], processID: "p" }] }] }));
    await using session = harness().open(new JaegerFileSource(file));
    await using lease = await session.tree(await session.select(), "t0");
    expect(lease.analysis.trace.spans.get("s")!.service).toBe("chat-server");
    await session.prepareView(lease.analysis, { full: true });
    expect(lease.analysis.trace.spans.get("s")!.attrs.payload).toBe("hello");
  } finally { await rm(path, { recursive: true, force: true }); }
});

test("shared real Jaeger fixture keeps complete analysis semantics in both loading modes", async () => {
  const file = fileURLToPath(new URL("../../../../conformance/trace/fixtures/genai-basic.jsonl", import.meta.url));
  const docs = (await readFile(file, "utf8")).trim().split("\n").map(line => JSON.parse(line));
  const h = new TraceHarness({ specs: genAiSpecs() });
  const expected = analysisSnapshot(await h.analyze(h.assemble(normalizeJaegerSpans(docs))));
  for (const lazy of [true, false]) {
    await using session = h.open(new JaegerFileSource(file), { config: { lazy } });
    const dataset = await session.select();
    for await (const ctx of session.trees(dataset)) {
      const analysis = await session.analyze(ctx); await session.prepareView(analysis, { full: true });
      expect(analysisSnapshot(analysis)).toEqual(expected);
    }
  }
});

test("malformed indexing leaves no completed cache", async () => {
  const path = await mkdtemp(join(tmpdir(), "jaeger-invalid-"));
  try {
    const file = join(path, "input.jsonl"); await writeFile(file, JSON.stringify(fixture.docs[0]) + "\ninvalid");
    const source = new JaegerFileSource(file, { indexDir: join(path, "index") });
    try { await expect(source.fetch("t0", [])).rejects.toThrow(); } finally { await source.close(); }
    expect(await readdir(join(path, "index"))).toEqual([]);
  } finally { await rm(path, { recursive: true, force: true }); }
});
