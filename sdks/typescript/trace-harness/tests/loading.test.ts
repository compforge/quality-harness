import { expect, test } from "bun:test";
import { mkdtemp, rm, access, readdir } from "node:fs/promises";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { Dataset, EvidenceDependency, EvidenceMissing, EvidenceTooLarge, FactDependency, TraceHarness,
  genAiSpecs, type FactProducer, type TraceContributions, analysisSnapshot } from "../src/index";
import { MemorySource, harness, fixture, deferred } from "./loading-fixture";

for (const item of fixture.cases) test(`shared loading contract: ${item.name}`, async () => {
  const source = new MemorySource();
  const session = harness().open(source, { config: { lazy: item.lazy, fields: item.fields } });
  try {
    const dataset = await session.select();
    expect(dataset.count).toBe(1); expect(source.fetches).toBe(0); expect(source.reads).toBe(0);
    const lease = await session.tree(dataset, "t0");
    const ctx = lease.analysis, node = ctx.trace.nodes[0]!, span = ctx.trace.spans.get("s")!;
    expect(span.fieldState("payload")).toBe(item.initial); expect(source.reads).toBe(item.initial_reads);
    expect(await Promise.all(Array.from({ length: 5 }, () => ctx.fact(node, "size")))).toEqual(Array(5).fill(fixture.expected.size));
    expect(source.reads).toBe(item.final_reads);
    expect((await ctx.measure(node, "self_ms"))!.values.self_ms).toBe(fixture.expected.self_ms);
    await ctx.runtime!.loader.load(ctx.trace, ["s"], Object.keys(fixture.expected.field_states));
    for (const [field, state] of Object.entries(fixture.expected.field_states)) expect(span.fieldState(field)).toBe(state);
    await lease.close(); await expect(ctx.fact(node, "size")).rejects.toThrow("lease");
    await lease.close();
  } finally { await session.close(); await session.close(); }
  expect(source.closes).toBe(1);
});

test("lazy/eager produce identical IR and full HTML; renderer does not read", async () => {
  const results = [];
  for (const lazy of [true, false]) {
    const source = new MemorySource();
    await using session = harness().open(source, { config: { lazy } });
    const dataset = await session.select();
    await using lease = await session.tree(dataset, "t0");
    const ctx = lease.analysis;
    await ctx.fact(ctx.trace.nodes[0]!, "size");
    const result = await session.analyze(ctx);
    await session.prepareView(result, { full: true });
    const reads = source.reads;
    results.push([analysisSnapshot(result), session.harness.renderInteractive(result.trace, result.findings, { measurements: result.measurements })]);
    expect(source.reads).toBe(reads);
  }
  expect(results[0]).toEqual(results[1]);
});

test("disk reuse, frozen spans, fresh computations and source isolation", async () => {
  const path = await mkdtemp(join(tmpdir(), "trace-cache-test-"));
  let dataset!: Dataset;
  try {
    const source = new MemorySource();
    await using first = harness().open(source, { workDir: path });
    dataset = await first.select();
    { await using lease = await first.tree(dataset, "t0"); await lease.analysis.fact(lease.analysis.trace.nodes[0]!, "size"); }
    // Reacquiring structural data must not evict cached detail fields.
    { await using lease = await first.tree(dataset, "t0"); expect(lease.analysis.trace.nodes[0]!.facts.size).toBeUndefined(); }
    const offline = new MemorySource(); offline.docs = {};
    await using second = harness().open(offline, { workDir: path });
    await using lease = await second.tree(await Dataset.load(dataset.path), "t0");
    expect(await lease.analysis.fact(lease.analysis.trace.nodes[0]!, "size")).toBe(5);
    expect(offline.fetches + offline.reads).toBe(0);
    offline.namespace = "other";
    await expect(second.tree(dataset, "t0")).rejects.toThrow("different source");
  } finally { await rm(path, { recursive: true, force: true }); }
});

test("concurrent leases share skeleton and evidence, not mutations", async () => {
  const source = new MemorySource();
  await using session = harness().open(source);
  const dataset = await session.select();
  await using a = await session.tree(dataset, "t0");
  await using b = await session.tree(dataset, "t0");
  expect(source.fetches).toBe(1); expect(a.analysis.trace.spans.get("s")).not.toBe(b.analysis.trace.spans.get("s"));
  const values = await Promise.all([a, b].map(lease => lease.analysis.fact(lease.analysis.trace.nodes[0]!, "size")));
  expect(values).toEqual([5, 5]); expect(source.reads).toBe(1);
  a.analysis.trace.nodes[0]!.facts.changed = true;
  expect(b.analysis.trace.nodes[0]!.facts.changed).toBeUndefined();
});

test("missing and failed evidence cannot masquerade as empty; unused preload failure is isolated", async () => {
  const source = new MemorySource(); source.read = async () => { throw new Error("offline"); };
  await using session = harness().open(source, { config: { lazy: false } });
  const dataset = await session.select();
  await using lease = await session.tree(dataset, "t0"); const ctx = lease.analysis, node = ctx.trace.nodes[0]!;
  expect(ctx.trace.spans.get("s")!.fieldState("payload")).toBe("failed");
  expect((await ctx.measure(node, "self_ms"))!.status).toBe("measured");
  await expect(ctx.fact(node, "size")).rejects.toThrow("offline");
  const missing = new MemorySource();
  await using other = harness().open(missing);
  await using absent = await other.tree(await other.select(), "t0");
  delete missing.docs.t0;
  await expect(absent.analysis.fact(absent.analysis.trace.nodes[0]!, "size")).rejects.toBeInstanceOf(EvidenceMissing);
});

for (const simultaneous of [false, true]) test(`fact dependency cycle never hangs: concurrent=${simultaneous}`, async () => {
  const producer = (name: string, dep: string): FactProducer => ({ produces: [name], applies: () => true,
    requires: node => [new FactDependency(node.node_id, dep)], compute: () => ({ [name]: 1 }) });
  await using session = harness({ factProducers: [producer("a", "b"), producer("b", "a")] }).open(new MemorySource());
  await using lease = await session.tree(await session.select(), "t0"); const ctx = lease.analysis, node = ctx.trace.nodes[0]!;
  const results = await Promise.allSettled((simultaneous ? ["a", "b"] : ["a"]).map(name => ctx.fact(node, name)));
  for (const result of results) { expect(result.status).toBe("rejected"); if (result.status === "rejected") expect(String(result.reason)).toContain("cyclic"); }
}, 2000);

test("multiple outputs compute once, including omitted outputs, with atomic validation", async () => {
  let calls = 0;
  await using session = harness({ factProducers: [{ produces: ["a", "b", "omitted"], applies: () => true,
    requires: node => [new EvidenceDependency(node.span_ids, ["payload"])], compute: () => { calls++; return { a: 1, b: 2 }; } }] }).open(new MemorySource());
  await using lease = await session.tree(await session.select(), "t0"); const ctx = lease.analysis, node = ctx.trace.nodes[0]!;
  expect(await Promise.all(["a", "b", "omitted"].map(name => ctx.fact(node, name)))).toEqual([1, 2, undefined]);
  expect(calls).toBe(1);
  await using invalid = harness({ factProducers: [{ produces: ["a"], applies: () => true, requires: () => [], compute: () => ({ a: 1, wrong: 2 }) }] }).open(new MemorySource());
  await using bad = await invalid.tree(await invalid.select(), "t0"); const n = bad.analysis.trace.nodes[0]!;
  await expect(bad.analysis.fact(n, "a")).rejects.toThrow("undeclared"); expect(n.facts.a).toBeUndefined();
});

test("detail projection and transform/measurer requirements prepare before pure compute", async () => {
  const custom: TraceContributions = {
    specs: [{ kind: "custom", matches: () => true, detail_fields: ["payload"], detail_facts: ["text"],
      build: span => span.attrs.payload === undefined ? {} : { text: span.attrs.payload },
      project_requires: ["upper"], project: node => [{ label: "text", value: String(node.facts.upper) }] }],
    transforms: [{ produces: ["upper"], applies: () => true, requires: node => [new FactDependency(node.node_id, "text")],
      compute: (node, ctx) => ({ upper: String(ctx.get(node, "text")).toUpperCase() }) }],
    measurers: [{ spec: { id: "size", scope: "node", units: {}, dimensions: [], description: "size" },
      requires: trace => trace.nodes.map(node => new FactDependency(node.node_id, "text")),
      compute: trace => trace.nodes.map(node => ({ spec_id: "size", anchor_node_id: node.node_id, status: "measured", values: { size: String(node.facts.text).length }, evidence: {}, error: null })) }],
    detectors: [async (node, ctx) => [{ ref: node.node_id, source: "custom", severity: "info", note: String(await ctx.fact(node, "upper")) }]],
  };
  const source = new MemorySource(); await using session = new TraceHarness(custom).open(source);
  await using lease = await session.tree(await session.select(), "t0"); const node = lease.analysis.trace.nodes[0]!;
  expect(node.brief).toEqual([]); expect(source.reads).toBe(0);
  const result = await session.analyze(lease.analysis, { metrics: ["size"] });
  expect(result.measurements.get(node.node_id, "size")!.values).toEqual({ size: 5 });
  await session.prepareView(result);
  expect(node.brief[0]!.value).toBe("HELLO"); expect(result.findings[node.node_id]!.some(f => f.note === "HELLO")).toBe(true);
  expect(source.reads).toBe(1);
});

test("preloading and demand loading both enforce trace byte limits", async () => {
  for (const lazy of [true, false]) {
    const source = new MemorySource(); (source.docs.t0!.tags as Array<{key:string,value:unknown}>).push({ key: "large", value: "x".repeat(20000) });
    await using session = harness().open(source, { config: { lazy, maxTraceBytes: 4096 } });
    const dataset = await session.select();
    if (!lazy) await expect(session.tree(dataset, "t0")).rejects.toBeInstanceOf(EvidenceTooLarge);
    else { await using lease = await session.tree(dataset, "t0"); await expect(session.prepareView(lease.analysis, { full: true })).rejects.toBeInstanceOf(EvidenceTooLarge); }
  }
});

test("active trace slots queue until lease release; close rejects waiters and cleans temporary workspace", async () => {
  const source = new MemorySource(); const session = harness().open(source, { config: { activeTraces: 1 } });
  const dataset = await session.select(), path = await session.workspace();
  const first = await session.tree(dataset, "t0"); let entered = false;
  const queued = session.tree(dataset, "t0").then(lease => { entered = true; return lease; });
  await Promise.resolve(); expect(entered).toBe(false);
  await first.close(); const second = await queued;
  expect(entered).toBe(true);
  const cancelled = session.tree(dataset, "t0"); const observed = Promise.allSettled([cancelled]);
  await session.close(); expect((await observed)[0]!.status).toBe("rejected");
  await second.close(); await session.close(); expect(source.closes).toBe(1);
  await expect(access(path)).rejects.toThrow();
});

test("session close aborts active Source I/O and settles all callers", async () => {
  const source = new MemorySource(), started = deferred(); let aborted = false;
  source.read = async (_refs, _fields, signal) => {
    started.resolve();
    return new Promise((_resolve, reject) => signal!.addEventListener("abort", () => { aborted = true; reject(signal!.reason); }, { once: true }));
  };
  const session = harness().open(source);
  const lease = await session.tree(await session.select(), "t0");
  const pending = lease.analysis.fact(lease.analysis.trace.nodes[0]!, "size");
  const observed = Promise.allSettled([pending]); await started.promise; await session.close();
  expect(aborted).toBe(true); expect((await observed)[0]!.status).toBe("rejected"); expect(source.closes).toBe(1);
});

test("partial selection is removed; closing a tree iterator releases its lease", async () => {
  const source = new MemorySource(); source.select = async function* () { yield "t0"; throw new Error("interrupted"); };
  await using session = harness().open(source);
  await expect(session.select()).rejects.toThrow("interrupted");
  expect(await readdir(join(await session.workspace(), "datasets"))).toEqual([]);
  await using good = harness().open(new MemorySource(), { config: { activeTraces: 1 } });
  const dataset = await good.select(); let retained;
  for await (const ctx of good.trees(dataset)) { retained = ctx; break; }
  await expect(retained!.fact(retained!.trace.nodes[0]!, "size")).rejects.toThrow("lease");
  await using next = await good.tree(dataset, "t0");
});


test("shared Source budget bounds concurrent reads across datasets", async () => {
  const source = new MemorySource();
  source.docs.t1 = { ...source.docs.t0!, traceID: "t1" };
  let active = 0, peak = 0;
  const read = source.read.bind(source);
  source.read = async (...args) => {
    active++; peak = Math.max(peak, active);
    try { await new Promise(resolve => setTimeout(resolve, 5)); return await read(...args); }
    finally { active--; }
  };
  await using session = harness().open(source, { config: { concurrency: 1, activeTraces: 4 } });
  const [a, b] = await Promise.all([session.select(), session.select()]);
  const leases = await Promise.all([session.tree(a, "t0"), session.tree(b, "t1")]);
  try { await Promise.all(leases.map(l => l.analysis.fact(l.analysis.trace.nodes[0]!, "size"))); }
  finally { await Promise.all(leases.map(l => l.close())); }
  expect(source.reads).toBe(2); expect(peak).toBe(1);
});

test("extending structural projection reads frozen identities without refetching", async () => {
  const path = await mkdtemp(join(tmpdir(), "trace-projection-"));
  try {
    const source = new MemorySource();
    await using first = harness().open(source, { workDir: path });
    const dataset = await first.select();
    { await using lease = await first.tree(dataset, "t0"); }
    const next = new MemorySource();
    next.fetch = async () => { throw new Error("must not refetch frozen skeleton"); };
    await using second = harness({ structureFields: ["payload"] }).open(next, { workDir: path });
    await using lease = await second.tree(dataset, "t0");
    expect(lease.analysis.trace.spans.get("s")!.attrs.payload).toBe("hello"); expect(next.reads).toBe(1);
  } finally { await rm(path, { recursive: true, force: true }); }
});

test("aliases participate in structural classification and detail dependencies", async () => {
  const source = new MemorySource();
  source.docs.t0!.tags = [{ key: "vendor.op", value: "chat" }, { key: "vendor.payload", value: "hello" }];
  await using session = harness({ fieldAliases: { "vendor.op": "gen_ai.operation.name", "vendor.payload": "payload" } }).open(source);
  await using lease = await session.tree(await session.select(), "t0");
  expect(lease.analysis.trace.nodes[0]!.kind).toBe("model-call");
  expect(await lease.analysis.fact(lease.analysis.trace.nodes[0]!, "size")).toBe(5);
});

test("streaming evidence is prepared before HTTP diagnosis", async () => {
  const source = new MemorySource();
  source.docs.t0!.operationName = "POST /chat";
  source.docs.t0!.tags = [{ key: "http.method", value: "POST" }, { key: "http.url", value: "http://chat/chat" },
    { key: "http.request.header.accept", value: "text/event-stream" }];
  await using session = harness().open(source);
  await using lease = await session.tree(await session.select(), "t0");
  const result = await session.analyze(lease.analysis);
  expect(Object.values(result.findings).flat().some(f => f.source === "http_slow_request")).toBe(false);
  expect(lease.analysis.trace.spans.get("s")!.fieldState("http.request.header.accept")).toBe("loaded");
  expect(source.reads).toBe(1);
});


test("Source normalization is not coupled to Jaeger raw document keys", async () => {
  const source = new MemorySource(), project = source.project.bind(source);
  source.project = (doc, fields) => {
    const span = project(doc, fields); span.raw = { protocol: "custom", attributes: span.attrs }; return span;
  };
  await using session = harness().open(source, { config: { lazy: false } });
  await using lease = await session.tree(await session.select(), "t0");
  expect(lease.analysis.trace.trace_id).toBe("t0");
  expect(lease.analysis.trace.spans.get("s")!.fieldState("payload")).toBe("loaded");
  expect(await lease.analysis.fact(lease.analysis.trace.nodes[0]!, "size")).toBe(5);
});

test("changed evidence identity never commits facts or a successful field state", async () => {
  const source = new MemorySource(), read = source.read.bind(source);
  source.read = async (...args) => {
    const spans = await read(...args); spans.get("s")!.storage_id = "replacement"; return spans;
  };
  await using session = harness().open(source);
  await using lease = await session.tree(await session.select(), "t0");
  const ctx = lease.analysis, node = ctx.trace.nodes[0]!;
  await expect(ctx.fact(node, "size")).rejects.toBeInstanceOf(EvidenceMissing);
  expect(node.facts.size).toBeUndefined(); expect(ctx.trace.spans.get("s")!.fieldState("payload")).toBe("failed");
});

test("measurement output order follows registration, independent of read completion", async () => {
  const h = harness({ measurers: [true, false].map((slow, i) => ({
    spec: { id: `m${i}`, scope: "node", units: {}, dimensions: [], description: "ordering" },
    requires: trace => slow ? [new EvidenceDependency([...trace.spans.keys()], ["payload"])] : [],
    compute: trace => trace.nodes.map(node => ({ spec_id: `m${i}`, anchor_node_id: node.node_id, status: "measured", values: { n: i }, evidence: {}, error: null })),
  })) });
  const outputs = [];
  for (const lazy of [true, false]) {
    await using session = h.open(new MemorySource(), { config: { lazy } });
    await using lease = await session.tree(await session.select(), "t0");
    const result = await session.analyze(lease.analysis, { metrics: ["m0", "m1"], diagnosis: false });
    expect(result.measurements.specs.map(item => item.id)).toEqual(["m0", "m1"]); outputs.push(result.measurements);
  }
  expect(outputs[0]).toEqual(outputs[1]);
});
