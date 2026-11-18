import { expect, test, mock } from "bun:test";
import { mkdtempSync, rmSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { fileURLToPath } from "node:url";
import { Engine, loadRun, writeRunData, buildCallgraph, entryCallgraph, renderPerfReport,
  validateLoadPlan, type Runner } from "../src";
const service = {name:"mock",component:{name:"api",repository:{forge:{name:"test"},path:"mock"}},environment:{name:"local"},workloads:[]};
const fixture = fileURLToPath(new URL("../../../../conformance/perf/fixtures/operation-metrics/", import.meta.url));

/** @case QPS=0 records real time but never acquires a business runner or invents request measurements. */
test("zero injection needs neither runner nor inflight cap and skips every hook", async () => {
  const unexpected = mock(async () => { throw new Error("business side effect"); });
  const runner: Runner = {name:"forbidden", setup:unexpected, fire:unexpected, deactivate:unexpected, cleanup:unexpected};
  for (const supplied of [undefined, runner]) {
    const run = await new Engine({service, runner:supplied, loads:[{request_rate:0,hold_s:0.02}]}).run();
    const arm = run.executions[0]!;
    expect(run.status).toBe("completed");
    expect(arm.phases.map(p=>p.kind)).toEqual(["hold"]);
    expect(arm.windows.map(w=>w.kind)).toEqual(["full","phase"]);
    expect(arm.windows[0]!.end_s).toBeGreaterThanOrEqual(0.02);
    expect(arm.started_at_s).toBeGreaterThan(Date.now()/1000-5);
    expect(arm.requests).toEqual([]);
    expect(arm.operation_runs).toEqual([]);
    expect(arm.windows.every(w=>w.request === undefined)).toBe(true);
    expect(renderPerfReport(run)).not.toContain("Injected request windows");
    const folder=mkdtempSync(join(tmpdir(),"perf-zero-"));
    try {writeRunData(run,folder);expect(loadRun(folder).executions[0]!.windows[0]!.request).toBeUndefined();}
    finally {rmSync(folder,{recursive:true,force:true});}
  }
  expect(unexpected).not.toHaveBeenCalled();
  expect(()=>validateLoadPlan({request_rate:1,hold_s:1})).toThrow("max_inflight");
});

test("manual stop seals hold and leaves cancellation signal usable for final collection", async () => {
  const stop=new AbortController(), cancel=new AbortController();
  let timer: ReturnType<typeof setTimeout>;
  const run=await new Engine({service, loads:[{request_rate:0,hold_s:60}], stopSignal:stop.signal, signal:cancel.signal,
    onArmStart:()=>{timer=setTimeout(()=>stop.abort(),20);}}).run();
  clearTimeout(timer!);
  expect(run.status).toBe("completed");
  expect(cancel.signal.aborted).toBe(false);
  expect(run.executions[0]!.stop.reason).toBe("manual");
  expect(run.executions[0]!.windows[0]).toMatchObject({complete:true,start_s:0});
  expect(run.executions[0]!.windows[0]!.end_s).toBeLessThan(1);
});

test("explicit entries choose descendants, union shared nodes and tolerate cycles", () => {
  const run=loadRun(fixture);
  const base=buildCallgraph(run)!;
  const nodes=base.arms[0]!.windows[0]!.nodes;
  const template=nodes[0]!;
  const snapshot={...base,entry_ids:["root-b","root-c"],relations:[
    {id:"1",kind:"call" as const,source:"root-b",target:"shared"},
    {id:"2",kind:"may_call" as const,source:"root-c",target:"shared"},
    {id:"3",kind:"call" as const,source:"shared",target:"root-b"},
  ],arms:base.arms.map(arm=>({...arm,windows:arm.windows.map(window=>({...window,
    nodes:["root-a","root-b","root-c","shared"].map(id=>({...template,id}))}))}))};
  const scoped=entryCallgraph(snapshot);
  expect(scoped.arms[0]!.windows[0]!.nodes.map(n=>n.id)).toEqual(["root-b","root-c","shared"]);
  expect(scoped.relations).toHaveLength(3);
  expect(snapshot.arms[0]!.windows[0]!.nodes).toHaveLength(4);
  const ids=run.graph!.nodes.filter(n=>n.kind==="operation").map(n=>n.id);
  run.entry_ids=[ids.at(-1)!];
  const view=buildCallgraph(run)!;
  expect(view.entry_ids).toEqual(run.entry_ids);
  const folder=mkdtempSync(join(tmpdir(),"perf-entry-"));
  try {writeRunData(run,folder);expect(loadRun(folder).entry_ids).toEqual(run.entry_ids);}
  finally {rmSync(folder,{recursive:true,force:true});}
  const html=renderPerfReport(run);
  expect(html).toContain('aria-selected="true" tabindex="0">Trends</button>');
  expect(html).toContain('aria-labelledby="rk-tab-1" data-wide="true" hidden');
  const graphContent = html.split('id="callgraph" class="perf-content graph-view">')[1]!.split("</div></div><script>")[0]!;
  expect([...graphContent.matchAll(/<summary>([^<]+)<\/summary>/g)].map(match => match[1]))
    .toEqual(["Callgraph", "完整窗口指标明细"]);
  expect(html).toContain("Operation metrics");
  expect(html).toContain("Case entry");expect(html).toContain("Full graph");
});
