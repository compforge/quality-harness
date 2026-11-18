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

test("offline report separates overview and trends and groups labeled samples by category", () => {
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
    category: "resource:cpu",
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
    const overview = html.split('id="rk-panel-0"')[1]!.split('id="rk-panel-1"')[0]!;
    expect(overview).toContain("执行阶段与流量统计");
    expect(overview).not.toContain('class="chart"');
    const trends = html.split('id="rk-panel-1"')[1]!.split('id="rk-panel-2"')[0]!;
    expect(trends).toContain('<summary>资源耗费</summary>');
    expect(trends).toContain('"text":"CPU"');
    expect(trends).toContain('request_qps');
    expect(html).not.toContain("压力响应曲线");
    expect(html).toContain("完整窗口指标明细");
  } finally {
    rmSync(tmp, { recursive: true, force: true });
  }
});

test("every time plot shares actual dispatch QPS across Cases, including a partial final bucket", () => {
  const run = loadRun(fixture), arm = run.executions[0]!;
  run.graph = undefined;
  const window = arm.windows.find(w => w.kind === "full")!;
  Object.assign(window, {start_s:0, end_s:2.5});
  const request = arm.requests[0]!;
  arm.requests = [
    {...request, case_id:"alpha", dispatched_at:0.1, finished_at:1.1, state:"finished"},
    {...request, case_id:"beta", dispatched_at:0.5, finished_at:2.1, state:"finished"},
    {...request, dispatched_at:undefined, finished_at:undefined, state:"dropped"},
    {...request, dispatched_at:2.1, finished_at:undefined, state:"interrupted"},
  ];
  const owner = "operation:mock/downstream";
  const labels = {operation:owner, service:"mock"};
  const queries = ["red:qps","resource:cpu","resource:mem","red:other",undefined].map((category,i) => {
    const metric = "prometheus_query."+owner+".series_"+i;
    arm.registry[metric] = {category, unit:"unit"};
    (arm.series ??= {})[metric+'{operation="'+owner+'",service="mock"}'] = [[0,0],[1,3]];
    return {...arm.prometheus_observations![0]!, metric,labels,step_s:1,window_id:null,error:null};
  });
  arm.prometheus_observations = queries;
  const html = renderPerfReport(run);
  expect(html).toContain('>Trends</button>');
  expect(html).not.toContain('>Callgraph</button>');
  const options = [...html.matchAll(/previousElementSibling\)\.setOption\((\{[^\n]+?\})\);<\/script>/g)]
    .map(match=>JSON.parse(match[1]!));
  expect(options.length).toBeGreaterThanOrEqual(8);
  for (const plot of options) {
    const pressure = plot.series.filter((s:{name:string})=>s.name==="request_qps");
    expect(pressure).toHaveLength(1);
    expect(pressure[0].data).toEqual([[0,2],[1,0],[2,2]]);
    if (plot.series.length > 1) expect(pressure[0].yAxisIndex).toBe(1);
  }
  expect(options.find(p=>p.title.text==="完成吞吐").series[0].data).toEqual([[0,0],[1,1],[2,2]]);
  const custom = html.split("<summary>自定义指标</summary>")[1]!;
  expect(custom).toContain("series_3");
  expect(custom).toContain("series_4");
  expect(custom).not.toContain("series_2");
});
