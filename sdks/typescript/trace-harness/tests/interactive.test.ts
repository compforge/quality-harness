import { expect, test } from "bun:test";
import { readFileSync } from "node:fs";
import { runInNewContext } from "node:vm";
import { Measurements } from "../src/model/measurement";
import { CALLS, SELF } from "../src/analyze/measure";
import { measurementRows } from "../src/view/measurements";

// Execute both embedded browser implementations against the same saved View contract.
const sources = [
  ["TypeScript", new URL("../src/view/interactive.ts", import.meta.url)],
  ["Python", new URL("../../../python/trace_harness/view/interactive.py", import.meta.url)],
] as const;
const rows = measurementRows(new Measurements([SELF, CALLS], [], { anchor: [
  { kind: SELF.kind, node_id: "anchor", status: "measured", values: { self_ms: 3 }, evidence: {}, error: null },
  { kind: CALLS.kind, node_id: "anchor", status: "measured",
    values: { http: { count: 2, duration_sum_ms: 15, covered_ms: 10 }, node: { count: 1, duration_sum_ms: 10, covered_ms: 10 } },
    evidence: { start_ms: 0, end_ms: 10 }, error: null },
] }), "anchor");

for (const [language, path] of sources) {
  const source = readFileSync(path, "utf8");
  const measurementScript = source.slice(source.indexOf("function measurementValue("), source.indexOf("function select("));
  const measurements = runInNewContext(measurementScript + "\n({measurementBlock, measurementGroup})", {
    esc: String, fmtMs: (value: number) => `${value}ms`,
  });

  test(`${language}: measurement identity and call dimension stay separate`, () => {
    const html = measurements.measurementBlock({ measurements: rows });
    expect(html.match(/class="measurement-group"/g)).toHaveLength(2);
    expect(html).toContain("<h4>self_ms</h4>");
    expect(html).toContain("<h4>累计调用</h4>");
    expect(html).toContain('<th scope="row">http</th>');
    expect(html).toContain('<th scope="row">node</th>');
    expect(html).not.toContain("undefined");
  });

  test(`${language}: node ownership does not imply a prefix measurement`, () => {
    const self = measurements.measurementGroup(rows.filter(row => row.kind === SELF.kind));
    expect(self).toContain("此节点内部");
    expect(self).not.toContain("请求开始");
    expect(self).not.toContain('class="measurement-note"');
    expect(self).not.toContain('scope="col">Kind');
    const prefix = measurements.measurementGroup(rows.filter(row => row.kind === CALLS.kind));
    expect(prefix).toContain("请求开始 → 此节点结束");
    expect(prefix).toContain("10ms wall-clock");
    expect(prefix).toContain('class="measurement-note"');
  });

  test(`${language}: paged flame layout retains saved depths for detached branches`, () => {
    const start = source.indexOf("function buildFlame(){");
    const end = source.indexOf("flameBuilt=true;paintSearch();}", start) + "flameBuilt=true;paintSearch();}".length;
    const cell = () => ({ style: {} as Record<string, string>, dataset: {} as Record<string, string>, clientWidth: 70, addEventListener() {} });
    const box = { style: {} as Record<string, string>, cells: [] as ReturnType<typeof cell>[],
      replaceChildren() { this.cells = []; }, appendChild(value: ReturnType<typeof cell>) { this.cells.push(value); } };
    const axis = { replaceChildren() {}, appendChild() {} };
    const node = (id: string, depth: number, children: unknown[] = []) => ({
      node_id: id, name: id, kind: "node", start_ms: 0, duration_ms: 10, depth, children,
    });
    const tree = { roots: [node("page-start", 2, [node("child", 3)]), node("other-branch", 1)] };
    const context = { tree, KCOLOR: {}, flameBuilt: false,
      document: { getElementById: (id: string) => id === "flame" ? box : axis, createElement: cell },
      fmtMs: String, compactName: () => "label", nameForBudget: () => "label", paintSearch() {},
    };
    runInNewContext(source.slice(start, end) + "\nbuildFlame();", context);
    expect(box.cells.map(c => [c.dataset.id, c.style.top])).toEqual([
      ["page-start", "40px"], ["child", "60px"], ["other-branch", "20px"],
    ]);
    expect(box.style.height).toBe("84px");
    expect(tree.roots.map(n => n.depth)).toEqual([2, 1]);
  });
}
