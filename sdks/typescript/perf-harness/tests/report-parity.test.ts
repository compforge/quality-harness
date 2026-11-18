import { expect, test } from "bun:test";
import { mkdtempSync, rmSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { fileURLToPath } from "node:url";
import { loadRun, writeRunData, renderPerfReport } from "../src";
const fixture = fileURLToPath(
  new URL(
    "../../../../conformance/perf/fixtures/operation-metrics/",
    import.meta.url,
  ),
);

test("eval-perf shows comparable scan levels and replays labeled probe samples offline", () => {
  const run = loadRun(fixture);
  const a = run.executions[0]!;
  a.arm.load = {
    request_rate: 1,
    max_inflight: 3,
    hold_s: 10,
    warmup: { step_s: 0 },
  };
  const b = {
    ...a,
    id: a.id + "-2",
    arm: { ...a.arm, id: "second", load: { ...a.arm.load, request_rate: 2 } },
  };
  run.executions = [a, b];
  const summary = a.windows.find((w) => w.kind === "full")!;
  summary.limited_s = 0;
  const sid = 'resource.cpu{pod="one",service="worker"}';
  a.registry["resource.cpu"] = {
    value_kind: "gauge",
    unit: "cores",
    side: "resource",
  };
  a.series = {
    [sid]: [
      [0, 0],
      [1, 1],
    ],
  };
  run.report_columns = [
    { title: "业务错误率", metric: "request.error_rate.value" },
  ];
  const tmp = mkdtempSync(join(tmpdir(), "perf-html-"));
  try {
    writeRunData(run, tmp);
    const saved = loadRun(tmp);
    expect(saved.executions[0]!.series).toEqual(a.series);
    expect(saved.report_columns).toEqual(run.report_columns);
    const html = renderPerfReport(saved);
    expect(html).toContain("业务错误率");
    expect(html).toContain("3. 压力响应曲线（指标随档位）");
    expect(html).toContain("worker · cores");
    expect(html).toContain("完整窗口指标明细");
    saved.executions[1]!.arm.load.max_inflight = 4;
    expect(renderPerfReport(saved)).not.toContain(
      "3. 压力响应曲线（指标随档位）",
    );
  } finally {
    rmSync(tmp, { recursive: true, force: true });
  }
});
