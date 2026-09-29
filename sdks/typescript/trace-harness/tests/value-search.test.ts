import { expect, test } from "bun:test";
import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import { runInNewContext } from "node:vm";
import { strFromU8 } from "fflate";
import { Node, TraceContext, renderInteractive } from "../src";
import { archiveEntries } from "./archive-fixture";

const sources = [
  resolve(import.meta.dir, "../src/view/interactive.ts"),
  resolve(import.meta.dir, "../../../python/trace_harness/view/interactive.py"),
];

for (const source of sources) test(`value-only search and navigation: ${source}`, () => {
  const text = readFileSync(source, "utf8");
  const start = text.indexOf("// Search only business-selected");
  const end = text.indexOf("searchNext.onclick=()=>moveSearch(1);", start) + "searchNext.onclick=()=>moveSearch(1);".length;
  const elements: Record<string, any> = {};
  const unfolded: string[] = [], selected: string[] = [];
  const rows = ["parent", "first", "second", "name-only"].map(id => {
    const classes = new Set<string>();
    return { dataset: { id }, classes, classList: {
      toggle: (name: string, on: boolean) => on ? classes.add(name) : classes.delete(name),
    }};
  });
  for (const id of ["node-search", "search-status", "search-prev", "search-next"]) {
    elements[id] = { value: "", textContent: "", disabled: false, listeners: {},
      addEventListener(event: string, fn: unknown) { this.listeners[event] = fn; } };
  }
  const byId = {
    parent: { name: "parent" }, first: { name: "http.server" }, second: { name: "http.server" },
    "name-only": { name: "/api/v1/resources", facts: { url: "/api/v1/resources" } },
  };
  const values: Record<string, string> = { first: "/API/v1/resources", second: "https://host/api/v1/resources?q=[x]" };
  const context = { perspective: "full", byId, treeEl: { querySelector: () => null },
    document: { getElementById: (id: string) => elements[id], querySelectorAll: () => rows },
    nodeSearchValue: (id: string) => values[id] ?? "",
    unfold: (id: string) => unfolded.push(id), unfoldAncestors: (id: string) => unfolded.push(id),
    select: (id: string) => selected.push(id), showLayout: () => {},
  };
  runInNewContext(text.slice(start, end), context);
  const input = elements["node-search"]!;
  input.value = " /api/v1/RESOURCES "; input.listeners.input();
  expect(unfolded).toEqual(["first", "second"]);
  expect(rows.filter(r => r.classes.has("search-hit")).map(r => r.dataset.id)).toEqual(["first", "second"]);
  expect(elements["search-status"].textContent).toBe("2 个命中");
  elements["search-next"].onclick(); elements["search-next"].onclick(); elements["search-next"].onclick();
  expect(selected).toEqual(["first", "second", "first"]);
  input.listeners.keydown({ key: "Enter", shiftKey: true, preventDefault() {} });
  expect(selected.at(-1)).toBe("second");
  input.value = "[x]"; input.listeners.input();
  expect(rows.filter(r => r.classes.has("search-hit")).map(r => r.dataset.id)).toEqual(["second"]);
  input.value = "missing"; input.listeners.input();
  expect(elements["search-status"].textContent).toBe("无匹配节点");
  expect(elements["search-next"].disabled).toBe(true);
  input.listeners.keydown({ key: "Escape" });
  expect(rows.every(r => !r.classes.has("search-hit"))).toBe(true);
  expect(elements["search-status"].textContent).toBe("");
  context.perspective = "agent"; input.value = "resources"; input.listeners.input();
  expect(input.disabled).toBe(true);
  expect(rows.every(r => !r.classes.has("search-hit"))).toBe(true);
});

test("offline search keeps complete values separate from the tree and raw details", () => {
  const value = "x".repeat(20000) + "/api/v1/resources</script>";
  const node = new Node({ kind: "http", name: "http.server", value, node_id: "root",
    primary_span_id: "root", span_ids: [], start_ms: 0, duration_ms: 1, facts: {} });
  const html = renderInteractive(new TraceContext("fixture", new Map(), [node], new Map()));
  const archive = archiveEntries(html);
  expect(JSON.parse(strFromU8(archive["values.json"]!))).toEqual({ root: value });
  expect(strFromU8(archive["index.json"]!)).not.toContain("resources");
  expect(html).toContain('id="node-search"');
  expect(html).not.toContain(value);
  const script = html.match(/<script>([\s\S]*)<\/script>/)![1]!;
  expect(() => new Function(script)).not.toThrow();
});
