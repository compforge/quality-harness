import { describe, expect, test } from "bun:test";
import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import {
  analysisSnapshot,
  assemble,
  buildView,
  DefaultFacet,
  diagnose,
  DisplayName,
  type DisplayNode,
  type FeatureContext,
  genAiSpecs,
  Node,
  normalizeJaegerSpans,
  nameProjections,
  renderInteractive,
  renderDisplay,
  TraceHarness,
} from "../src";

function fixtureDocuments(): Array<Record<string, unknown>> {
  const path = resolve(import.meta.dir, "../../../../conformance/trace/fixtures/genai-basic.jsonl");
  return readFileSync(path, "utf8").trim().split("\n").map((line) => JSON.parse(line));
}

describe("Python trace_harness parity fixture", () => {
  test("normalizes and assembles the same logical node set", () => {
    const context = assemble(normalizeJaegerSpans(fixtureDocuments()), genAiSpecs());
    expect(context.trace_id).toBe("abc123fixturetrace0000000000000001");
    expect(context.spans.size).toBe(6);
    expect(context.nodes
      .sort((a, b) => a.start_ms - b.start_ms)
      .map((node) => [node.kind, node.name, node.parent_node_id ?? null]))
      .toEqual([
        ["agent", "invoke_agent main", null],
        ["model-call", "chat planner", "1111111111111111"],
        ["http", "POST /chat/completions", "2222222222222222"],
        ["tool-call", "execute_tool web_search", "1111111111111111"],
        ["model-call", "chat synth", "1111111111111111"],
        ["http", "POST /chat/completions", "5555555555555555"],
      ]);
    const planner = context.nodes.find((node) => node.name === "chat planner")!;
    expect(planner.facts).toMatchObject({
      model: "model-alpha-seed-2",
      in_tokens: 1820,
      out_tokens: 640,
      self_ms: 100,
      http_status: 200,
    });
  });

  test("renders the Python interactive view contract as one HTML file", () => {
    const context = assemble(normalizeJaegerSpans(fixtureDocuments()), genAiSpecs());
    const html = renderInteractive(context, diagnose(context));
    expect(html).toStartWith("<!doctype html>");
    expect(html).toContain("调用栈");
    expect(html).toContain("火焰图");
    expect(html).toContain('data-perspective="full"');
    expect(html).not.toContain('data-perspective="agent"');
    expect(html).toContain('data-layout="tree"');
    expect(html).toContain('data-layout="flame"');
    expect(html).toContain("chat planner");
    expect(html).toContain("http_status");
    expect(html).toContain("errors 2");
    expect(html).toContain('role="separator"');
    expect(html).toContain("requestAnimationFrame(applyTreeWidth)");
    expect(html).toContain("refreshTreeNames()");
    const browserScript = html.match(/<script>([\s\S]*)<\/script>/)?.[1];
    expect(browserScript).toBeDefined();
    expect(() => new Function(browserScript!)).not.toThrow();
  });

  test("renders object and stringified JSON attributes as trees with text fallback", () => {
    const documents = fixtureDocuments();
    const tags = documents[0]!.tags as Array<Record<string, unknown>>;
    tags.push(
      { key: "json.object", value: { nested: [1, true, null] } },
      { key: "json.encoded", value: JSON.stringify({ answer: "ok" }) },
      { key: "json.double_encoded", value: JSON.stringify(JSON.stringify({ answer: "ok" })) },
      { key: "text.plain", value: "not json" },
    );

    const context = assemble(normalizeJaegerSpans(documents), genAiSpecs());
    const html = renderInteractive(context);

    expect(html).toContain('"json.object":{"kind":"json","value":{"nested":[1,true,null]}}');
    expect(html).toContain('"json.encoded":{"kind":"json","value":{"answer":"ok"}}');
    expect(html).toContain('"json.double_encoded":{"kind":"json","value":{"answer":"ok"}}');
    expect(html).toContain('"text.plain":{"kind":"text","value":"not json"}');
    expect(html).toContain("var module, window, define, renderjson=");
    expect(html).toContain("dd.appendChild(renderjson(payload.value))");
    expect(html).not.toContain("function jsonTree(value,label,depth)");
  });

  test("uses shared budget compaction for Node Tree and flame graph tool names", () => {
    const context = assemble(normalizeJaegerSpans(fixtureDocuments()), genAiSpecs());
    const tool = context.nodes.find((node) => node.kind === "tool-call")!;
    tool.facts.tool = "shell";
    context.spans.get(tool.primary_span_id)!.attrs["gen_ai.tool.call.arguments"] = JSON.stringify({
      command: "python3 references/scripts/stream_query.py --question example",
    });

    const html = renderInteractive(context);

    expect(html).toContain('"name_variants":["shell · stream_query.py","stream_query.py","shell"]');
    expect(html).toContain("applyNameLayout(row,n,depth,rowHeight)");
    expect(html).toContain("nameForBudget(n,Math.max");
    expect(html).toContain("actual-1/rawLength");
  });

  test("treats the name ratio as a target without requiring an exact output ratio", () => {
    const name = new DisplayName("shell", "stream_query.py");

    expect(name.compact(1)).toEqual(["shell · stream_query.py", 1]);
    const [compacted, actual] = name.compact(0.5);
    expect(compacted).not.toBe("shell · stream_query.py");
    expect(actual).toBeLessThan(0.5);
    expect(nameProjections(name)).toEqual(["shell · stream_query.py", "stream_query.py", "shell"]);

    const plain: DisplayNode = {
      kind: "node", name: "plain", brief: [], node_ids: [], children: [], findings: [], folded: 0,
    };
    expect(nameProjections(plain)).toEqual(["plain"]);
  });

  test("jumps between the ratios reported by compact names", () => {
    let calls = 0;
    const sparseName = {
      compact(expect: number): readonly [string, number] {
        calls += 1;
        if (expect >= 1) return ["x".repeat(1000), 1];
        if (expect >= 0.5) return ["m".repeat(500), 0.5];
        return ["s".repeat(100), 0.1];
      },
    };

    expect(nameProjections(sparseName)).toEqual([
      "x".repeat(1000), "m".repeat(500), "s".repeat(100),
    ]);
    expect(calls).toBe(4);
  });
});

describe("trace perspectives", () => {
  const node = (
    nodeId: string,
    kind: string,
    parentNodeId?: string,
    startMs = 0,
    error = false,
  ): Node => new Node({
    kind,
    name: `${nodeId}.${kind}`,
    primary_span_id: nodeId,
    span_ids: [nodeId],
    facts: {},
    start_ms: startMs,
    duration_ms: 10,
    node_id: nodeId,
    parent_node_id: parentNodeId,
    error_span_ids: error ? [nodeId] : [],
  });

  test("agent perspective keeps semantic nodes and compresses context paths", () => {
    const root = node("root", "service");
    const agent = node("agent", "agent", root.node_id, 1);
    const bridge = node("bridge", "service", agent.node_id, 2);
    const model = node("model", "model-call", bridge.node_id, 3);
    const tool = node("tool", "tool-call", agent.node_id, 4);
    const noise = node("noise", "service", agent.node_id, 5);
    const errorHttp = node("error-http", "http", model.node_id, 6, true);
    const roots = renderDisplay(
      buildView([root, agent, bridge, model, tool, noise, errorHttp]),
      {},
      undefined,
      { perspective: "agent" },
    );
    const flat: Array<{ kind: string; name: string }> = [];
    const stack = [...roots];
    while (stack.length) {
      const display = stack.pop()!;
      flat.push(display);
      stack.push(...display.children);
    }

    expect(flat.filter((display) => display.kind).map((display) => display.name).sort())
      .toEqual([agent.name, model.name, tool.name].sort());
    expect(flat.some((display) => display.name.includes("上下文节点"))).toBe(true);
    expect(flat.map((display) => display.name)).not.toContain(noise.name);
    expect(flat.map((display) => display.name)).not.toContain(errorHttp.name);
    expect(model.parent_node_id).toBe(bridge.node_id);
  });
});

describe("shared conformance", () => {
  test("matches the canonical Analysis IR", async () => {
    const expected = await Bun.file(new URL(
      "../../../../conformance/trace/cases/genai-basic.analysis.json",
      import.meta.url,
    )).json();
    const harness = new TraceHarness({ specs: genAiSpecs() });
    const context = harness.assemble(normalizeJaegerSpans(fixtureDocuments()));

    expect(analysisSnapshot(context, harness.diagnose(context))).toEqual(expected);
  });
});

describe("scoped TraceHarness", () => {
  test("does not leak Plugin contributions between harnesses", () => {
    class AlphaModelFacet extends DefaultFacet {
      override priority = 100;
      match(node: Node): boolean {
        return node.kind === "model-call";
      }
    }

    const alpha = new TraceHarness({
      specs: genAiSpecs(),
      features: [{
        produces: ["scope_marker"],
        applies: (node) => node.kind === "agent",
        compute: () => ({ scope_marker: "alpha" }),
        bake: true,
      }, {
        produces: ["scope_action"],
        applies: (node) => node.kind === "agent",
        compute: (_node: Node, _context: FeatureContext) => ({ scope_action: "alpha-action" }),
        bake: false,
      }],
      detectors: [(node) => node.facts.scope_marker === "alpha" ? [{
        ref: node.node_id,
        source: "scope:alpha",
        severity: "info",
      }] : []],
      facets: [new AlphaModelFacet()],
    });
    const plain = new TraceHarness({ specs: genAiSpecs() });

    const alphaContext = alpha.assemble(normalizeJaegerSpans(fixtureDocuments()));
    const plainContext = plain.assemble(normalizeJaegerSpans(fixtureDocuments()));
    const alphaAgent = alphaContext.nodes.find((node) => node.kind === "agent")!;
    const plainAgent = plainContext.nodes.find((node) => node.kind === "agent")!;

    expect(alphaAgent.facts.scope_marker).toBe("alpha");
    expect(plainAgent.facts.scope_marker).toBeUndefined();
    expect(alpha.lazyFeatures(alphaAgent, alphaContext)).toEqual({ scope_action: "alpha-action" });
    expect(plain.lazyFeatures(plainAgent, plainContext)).toEqual({});
    const alphaFindings = alpha.diagnose(alphaContext);
    const plainFindings = plain.diagnose(plainContext);
    expect(alphaFindings[alphaAgent.node_id]?.map((finding) => finding.source))
      .toContain("scope:alpha");
    expect(Object.values(plainFindings).flat().map((finding) => finding.source))
      .not.toContain("scope:alpha");
    const displayedIds = (roots: ReturnType<TraceHarness["renderDisplay"]>): Set<string> => {
      const ids = new Set<string>();
      const stack = [...roots];
      while (stack.length) {
        const display = stack.pop()!;
        if (display.kind) for (const nodeId of display.node_ids) ids.add(nodeId);
        stack.push(...display.children);
      }
      return ids;
    };
    const successHttp = alphaContext.nodes.find((node) => (
      node.kind === "http" && node.facts.status === 200
    ))!;
    expect(displayedIds(alpha.renderDisplay(alphaContext, alphaFindings))).toContain(successHttp.node_id);
    expect(displayedIds(plain.renderDisplay(plainContext, plainFindings))).not.toContain(successHttp.node_id);
    expect(alpha.renderInteractive(alphaContext, alphaFindings)).toContain("alpha-action");
    expect(plain.renderInteractive(plainContext, plainFindings)).not.toContain("alpha-action");
  });
});
