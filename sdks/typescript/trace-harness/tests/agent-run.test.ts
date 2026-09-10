import { archiveContents, traceTrees } from "./archive-fixture";
import { describe, expect, test } from "bun:test";
import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import {
  agentRunSnapshot,
  createAgentRunIR,
  genAiSpecs,
  normalizeJaegerSpans,
  TraceHarness,
  type AgentRunIR,
  type TraceContext,
} from "../src";
import { agentRunRoots } from "../src/view/agent-run";

function fixtureDocuments(): Array<Record<string, unknown>> {
  const path = resolve(import.meta.dir, "../../../../conformance/trace/fixtures/genai-basic.jsonl");
  return readFileSync(path, "utf8").trim().split("\n").map((line) => JSON.parse(line));
}

class FixtureAgentRunExtractor {
  extract(context: TraceContext): AgentRunIR {
    const byName = new Map(context.nodes.map((node) => [node.name, node]));
    const agent = byName.get("invoke_agent main")!;
    const planner = byName.get("chat planner")!;
    const tool = byName.get("execute_tool web_search")!;
    const synth = byName.get("chat synth")!;
    return createAgentRunIR(context.trace_id, [{
      id: "run-main",
      name: "main-agent",
      start_ms: agent.start_ms,
      duration_ms: agent.duration_ms,
      status: "error",
      attributes: {},
      source_node_ids: [agent.node_id],
      items: [{
        kind: "operation",
        id: "initialize-1",
        name: "run.initialize",
        start_ms: agent.start_ms,
        duration_ms: 100,
        status: "completed",
        attributes: {},
        source_node_ids: [agent.node_id],
        operations: [{
          kind: "operation",
          id: "load-context-1",
          name: "context.load",
          start_ms: agent.start_ms + 10,
          duration_ms: 50,
          status: "completed",
          attributes: {},
          source_node_ids: [agent.node_id],
        }],
      }, {
        kind: "agent-turn",
        id: "turn-plan",
        name: "Plan and search",
        start_ms: planner.start_ms,
        duration_ms: 4750,
        status: "",
        attributes: {},
        source_node_ids: [],
        items: [{
          kind: "model-call",
          id: "model-plan",
          name: "planner",
          model: "model-alpha-seed-2",
          start_ms: planner.start_ms,
          duration_ms: planner.duration_ms,
          status: "completed",
          input: [{ role: "user", content: "Find a source" }],
          output: { tool_calls: [{ name: "web_search" }] },
          attributes: { input_tokens: 1820, output_tokens: 640 },
          source_node_ids: [planner.node_id],
        }, {
          kind: "tool-call",
          id: "tool-search",
          name: "web_search",
          tool_call_id: "call-search-1",
          start_ms: tool.start_ms,
          duration_ms: tool.duration_ms,
          status: "completed",
          input: { query: "example" },
          output: { matches: 1 },
          attributes: {},
          source_node_ids: [tool.node_id],
          agent_runs: [{
            id: "run-worker",
            name: "research-worker",
            start_ms: 1700000003650,
            duration_ms: 1100,
            status: "",
            attributes: {},
            source_node_ids: [tool.node_id],
            items: [{
              kind: "agent-turn",
              id: "turn-worker",
              name: "Research",
              start_ms: 1700000003650,
              duration_ms: 1100,
              status: "",
              attributes: {},
              source_node_ids: [],
              items: [{
                kind: "model-call",
                id: "model-worker",
                name: "worker-model",
                model: "model-alpha-seed-2",
                start_ms: 1700000003650,
                duration_ms: 1100,
                status: "completed",
                attributes: {},
                source_node_ids: [tool.node_id],
              }],
            }],
          }],
        }, {
          kind: "operation",
          id: "compact-1",
          name: "context.compact",
          start_ms: 1700000004800,
          duration_ms: 50,
          status: "completed",
          input: { messages: 12 },
          output: { messages: 4 },
          attributes: {},
          source_node_ids: [agent.node_id],
        }],
      }, {
        kind: "operation",
        id: "checkpoint-1",
        name: "framework.checkpoint",
        start_ms: 1700000004850,
        duration_ms: 25,
        status: "completed",
        attributes: {},
        source_node_ids: [agent.node_id],
      }, {
        kind: "agent-turn",
        id: "turn-answer",
        name: "Answer",
        start_ms: 1700000004875,
        duration_ms: 2525,
        status: "",
        attributes: {},
        source_node_ids: [],
        items: [{
          kind: "operation",
          id: "wrap-up-1",
          name: "turn.wrap_up",
          start_ms: 1700000004875,
          duration_ms: 25,
          status: "completed",
          attributes: {},
          source_node_ids: [agent.node_id],
        }, {
          kind: "model-call",
          id: "model-answer",
          name: "synthesizer",
          model: "model-alpha-seed-2",
          start_ms: synth.start_ms,
          duration_ms: synth.duration_ms,
          status: "error",
          input: [{ role: "user", content: "Answer with the source" }],
          attributes: {},
          source_node_ids: [synth.node_id],
        }],
      }, {
        kind: "operation",
        id: "finalize-1",
        name: "run.finalize",
        start_ms: 1700000007400,
        duration_ms: 600,
        status: "completed",
        attributes: {},
        source_node_ids: [agent.node_id],
      }],
    }]);
  }
}

describe("AgentRun IR", () => {
  const harness = () => new TraceHarness({
    specs: genAiSpecs(),
    agentRunExtractor: new FixtureAgentRunExtractor(),
  });

  test("matches the shared AgentRun IR conformance case", async () => {
    const instance = harness();
    const context = instance.assemble(normalizeJaegerSpans(fixtureDocuments()));
    const expected = await Bun.file(new URL(
      "../../../../conformance/trace/cases/genai-basic.agent-run.json",
      import.meta.url,
    )).json();

    expect(agentRunSnapshot(instance.extractAgentRuns(context)!)).toEqual(expected);
  });

  test("renders the extracted turns, calls, and operations", () => {
    const instance = harness();
    const context = instance.assemble(normalizeJaegerSpans(fixtureDocuments()));
    const html = instance.renderInteractive(context, instance.diagnose(context));

    expect(html + archiveContents(html)).toContain('data-perspective="agent"');
    expect(html + archiveContents(html)).toContain("agent-run:run-main");
    expect(html + archiveContents(html)).toContain("run.initialize");
    expect(html + archiveContents(html)).toContain("context.load");
    expect(html + archiveContents(html)).toContain("context.compact");
    expect(html + archiveContents(html)).toContain("turn.wrap_up");
    expect(html + archiveContents(html)).toContain("framework.checkpoint");
    expect(html + archiveContents(html)).toContain("run.finalize");
    expect(html + archiveContents(html)).toContain("agent-run:run-worker");
    expect(html + archiveContents(html)).toContain("worker-model");
    expect(html + archiveContents(html)).toContain("nodeNameLayout(depth,rowHeight)");
    expect(html + archiveContents(html)).toContain("applyNameLayout(row,n,depth,rowHeight)");
    expect(html + archiveContents(html)).toContain("timeHeight(n.duration_ms,stackMaxDuration)");
    expect(html + archiveContents(html)).toContain("const timedLeaf=perspective==='agent'&&!n.children.length");
    expect(html + archiveContents(html)).toContain("maxLeafDuration(tree.roots)");
    expect(html + archiveContents(html)).toContain("const base=22,max=base*4");
    expect(html + archiveContents(html)).toContain(".row.agent-row::before");
    expect(html + archiveContents(html)).toContain("row.classList.add('agent-row')");
    expect(html + archiveContents(html)).toContain("row.style.setProperty('--node-color',KCOLOR[n.kind]||'#9ca3af')");
    expect(html + archiveContents(html)).toContain("row.style.setProperty('--node-indent',(depth*16+4)+'px')");
    expect(html + archiveContents(html)).not.toContain("call=call-search-1");
    expect(html + archiveContents(html)).toContain('"tool_call_id":"call-search-1"');
  });

  test("uses file and command names instead of call IDs for tool display", () => {
    const instance = harness();
    const context = instance.assemble(normalizeJaegerSpans(fixtureDocuments()));
    const ir = createAgentRunIR(context.trace_id, [{
      id: "run",
      name: "run",
      start_ms: 0,
      duration_ms: 1,
      items: [{
        kind: "agent-turn",
        id: "turn",
        name: "turn",
        start_ms: 0,
        duration_ms: 1,
        items: [{
          kind: "tool-call",
          id: "read",
          name: "read_file",
          start_ms: 0,
          duration_ms: 0,
          tool_call_id: "call-read",
          input: { path: "/workspace/references/market.md" },
        }, {
          kind: "tool-call",
          id: "shell",
          name: "shell",
          start_ms: 0,
          duration_ms: 0,
          tool_call_id: "call-shell",
          input: {
            command: "cd /workspace/skills/demo && python3 references/scripts/stream_query.py --question example 2>&1",
          },
        }],
      }],
    }]);

    const items = ((agentRunRoots(context, ir, {})[0]!.children as Array<Record<string, unknown>>)[0]!
      .children as Array<Record<string, unknown>>);

    expect(items[0]!.name_variants).toEqual(["read_file · market.md", "market.md"]);
    expect(items[1]!.name_variants).toEqual(["shell · stream_query.py", "stream_query.py", "shell"]);
    expect(items[0]!.brief).toBe("");
    expect(items[1]!.brief).toBe("");
    expect(items[0]!.facts).toMatchObject({ tool_call_id: "call-read" });
    expect(items[1]!.facts).toMatchObject({ tool_call_id: "call-shell" });
  });

  test("collapses operations unless their subtree contains an error", () => {
    const instance = harness();
    const context = instance.assemble(normalizeJaegerSpans(fixtureDocuments()));
    const ir = createAgentRunIR(context.trace_id, [{
      id: "run",
      name: "run",
      start_ms: 0,
      duration_ms: 1,
      items: [{
        kind: "operation",
        id: "quiet",
        name: "quiet",
        start_ms: 0,
        duration_ms: 1,
        operations: [{
          kind: "operation",
          id: "child",
          name: "child",
          start_ms: 0,
          duration_ms: 1,
        }],
      }, {
        kind: "operation",
        id: "failed-parent",
        name: "failed-parent",
        start_ms: 0,
        duration_ms: 1,
        operations: [{
          kind: "operation",
          id: "failed",
          name: "failed",
          start_ms: 0,
          duration_ms: 1,
          status: "error",
        }],
      }],
    }]);

    const operations = agentRunRoots(context, ir, {})[0]!.children as Array<Record<string, unknown>>;

    expect(operations[0]!.collapsed).toBe(true);
    expect(operations[1]!.collapsed).toBe(false);
  });

  test("hides the Agent view when no extractor is contributed", () => {
    const instance = new TraceHarness({ specs: genAiSpecs() });
    const context = instance.assemble(normalizeJaegerSpans(fixtureDocuments()));

    expect(instance.renderInteractive(context)).not.toContain('data-perspective="agent"');
  });

  test("rejects source references outside the node tree", () => {
    const instance = new TraceHarness({
      specs: genAiSpecs(),
      agentRunExtractor: {
        extract: (context) => createAgentRunIR(context.trace_id, [{
          id: "broken",
          name: "broken",
          start_ms: 0,
          duration_ms: 0,
          source_node_ids: ["missing-node"],
          items: [],
        }]),
      },
    });
    const context = instance.assemble(normalizeJaegerSpans(fixtureDocuments()));

    expect(() => instance.extractAgentRuns(context)).toThrow("unknown node IDs");
  });

  test("rejects items outside their parent time window", () => {
    const instance = new TraceHarness({
      specs: genAiSpecs(),
      agentRunExtractor: {
        extract: (context) => createAgentRunIR(context.trace_id, [{
          id: "broken",
          name: "broken",
          start_ms: 100,
          duration_ms: 100,
          items: [{
            kind: "operation",
            id: "outside",
            name: "outside",
            start_ms: 50,
            duration_ms: 10,
          }],
        }]),
      },
    });
    const context = instance.assemble(normalizeJaegerSpans(fixtureDocuments()));

    expect(() => instance.extractAgentRuns(context)).toThrow("outside AgentRun broken time window");
  });

  test("rejects nested operations outside their parent time window", () => {
    const instance = new TraceHarness({
      specs: genAiSpecs(),
      agentRunExtractor: {
        extract: (context) => createAgentRunIR(context.trace_id, [{
          id: "broken",
          name: "broken",
          start_ms: 0,
          duration_ms: 300,
          items: [{
            kind: "operation",
            id: "outer",
            name: "outer",
            start_ms: 100,
            duration_ms: 100,
            operations: [{
              kind: "operation",
              id: "nested-outside",
              name: "nested-outside",
              start_ms: 50,
              duration_ms: 10,
            }],
          }],
        }]),
      },
    });
    const context = instance.assemble(normalizeJaegerSpans(fixtureDocuments()));

    expect(() => instance.extractAgentRuns(context)).toThrow("outside Operation outer time window");
  });
});
