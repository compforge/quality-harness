import { afterEach, expect, test } from "bun:test";
import { cpSync, mkdtempSync, readFileSync, readdirSync, rmSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { fileURLToPath } from "node:url";
import { Engine, loadRun, writeRunData, serializeRequests, planMetrics, collectMetrics, recollectMetrics,
  buildCallgraph, renderPerfReport, writePerfReport, type PrometheusProbe, type Run } from "../src";
import type { MetricDBReader } from "@compforge/harness-toolbox/metricdb";

const fixture = fileURLToPath(new URL("../../../../conformance/perf/fixtures/operation-metrics/",import.meta.url));
const folders: string[] = [];
afterEach(() => { for (const folder of folders.splice(0)) rmSync(folder,{recursive:true,force:true}); });
function directory(): string { const path = mkdtempSync(join(tmpdir(),"perf-metrics-")); folders.push(path); return path; }
function run(): Run { return loadRun(fixture); }
function source(query: MetricDBReader["query"]): MetricDBReader {
  return {url:"http://metrics",kind:"prometheus",query,queryRange: async () => {throw new Error("unexpected range");}};
}
const scalar = (value: string) => ({data:{resultType:"vector",result:[{metric:{},value:[1001,value]}]},warnings:[],infos:[]});
const probe: PrometheusProbe = {target:"operation:mock/chat",service:"mock",method:"POST",path:"/chat",response_mode:"sse",
  summaries:[{name:"qps",unit:"rps",promql:"sum(rate(requests[$window]))",description:"completed requests"}],metrics:{qps:"qps"}};
function unplanned(): Run {
  const value=run();
  for (const arm of value.executions) {arm.prometheus_observations=[];arm.probe_errors={};for (const window of arm.windows) window.probe_metrics={};}
  return value;
}

test("schema 2 callgraph matches Python fixture, preserves properties, zero, missing, error and +Inf", () => {
  const value = run(), graph = JSON.stringify(value.graph);
  expect(buildCallgraph(value)).toEqual(JSON.parse(readFileSync(join(fixture,"callgraph.json"),"utf8")));
  expect(JSON.stringify(value.graph)).toBe(graph);
  const path=directory();writeRunData(value,path);
  expect(buildCallgraph(loadRun(path))).toEqual(buildCallgraph(value));
  expect(serializeRequests(loadRun(path))).toBe(serializeRequests(value));
});

test("plan freezes real half-open windows and rejects backend/ownership/duplicate mismatches before I/O", async () => {
  const value=unplanned(), arm=value.executions[0]!, window=arm.windows[0]!;
  planMetrics(value,[probe],{url:"http://metrics",kind:"prometheus"});
  const saved=arm.prometheus_observations![0]!;
  expect(saved.start_s).toBe(Math.ceil((arm.started_at_s!+window.start_s)*1000)/1000);
  expect(saved.end_s).toBe((Math.ceil((arm.started_at_s!+window.end_s)*1000)-1)/1000);
  expect(saved.expression).not.toContain("$window");
  expect(() => planMetrics(value,[probe],{url:"http://metrics",kind:"prometheus"})).toThrow("frozen");
  expect(() => planMetrics(unplanned(),[probe,{...probe}],{url:"http://metrics",kind:"prometheus"})).toThrow("unique");
  expect(() => planMetrics(unplanned(),[{...probe,service:"other"}],{url:"http://metrics",kind:"prometheus"})).toThrow("owner");
  let calls=0;
  await expect(collectMetrics(value,[{...source(async()=>{calls++;return scalar("1");}),kind:"victoriametrics"}])).rejects.toThrow("backend");
  expect(calls).toBe(0);
});

test("recollection replaces only owned metric projections, archives attempts, and leaves request bytes unchanged", async () => {
  const path=directory();cpSync(fixture,path,{recursive:true});
  const value=unplanned();planMetrics(value,[probe],{url:"http://metrics",kind:"prometheus"});writeRunData(value,path);
  const requests=readFileSync(join(path,"requests.jsonl")), evaluations=readFileSync(join(path,"evaluations.json"));
  const good=await recollectMetrics(path,[source(async()=>scalar("0"))]);
  expect(buildCallgraph(good)!.arms[0]!.windows[0]!.nodes[0]!.properties.perf.metrics.qps!.value).toBe(0);
  const failed=await recollectMetrics(path,[source(async()=>{throw new Error("HTTP 503");})]);
  const metric=buildCallgraph(failed)!.arms[0]!.windows[0]!.nodes[0]!.properties.perf.metrics.qps!;
  expect(metric).toMatchObject({value:null,status:"error",reason:"query_error"});
  expect(failed.status).toBe(good.status);
  expect(readFileSync(join(path,"requests.jsonl"))).toEqual(requests);
  expect(readFileSync(join(path,"evaluations.json"))).toEqual(evaluations);
  expect(readdirSync(join(path,"metric-collections"))).toHaveLength(4);
});

test("empty samples stay missing; warnings, unaggregated labels, NaN and invalid ratios stay errors", async () => {
  for (const [response,status] of [
    [{data:{resultType:"vector",result:[]},warnings:[],infos:[]},"missing"],
    [{...scalar("1"),warnings:["partial response"]},"error"],
    [{data:{resultType:"vector",result:[{metric:{pod:"a"},value:[1001,"1"]}]},warnings:[],infos:[]},"error"],
    [scalar("NaN"),"error"], [scalar("+Inf"),"error"], [scalar("1.1"),"error"],
  ] as const) {
    const value=unplanned();
    planMetrics(value,[{...probe,summaries:[{name:"errors",unit:"ratio",promql:"errors"}],metrics:{error_rate:"errors"}}],{url:"http://metrics",kind:"prometheus"});
    await collectMetrics(value,[source(async()=>response as Awaited<ReturnType<MetricDBReader["query"]>>)]);
    expect(buildCallgraph(value)!.arms[0]!.windows[0]!.nodes[0]!.properties.perf.metrics.error_rate!.status).toBe(status);
    if (status === "missing") expect(buildCallgraph(value)!.arms[0]!.windows[0]!.nodes[0]!.properties.perf.metrics.error_rate!.reason).toBe("empty_result");
  }
});

test("classic histogram retains overflow, rejects non-monotonic buckets, and zero population stays missing", async () => {
  for (const [counts,status,expected] of [[[3,10],"observed","+Inf"],[[5,10],"observed",1],[[11,10],"error",null],[[0,0],"missing",null]] as const) {
    const value=unplanned();
    planMetrics(value,[{...probe,summaries:[{name:"tail",unit:"s",promql:"buckets",histogram:{quantile:0.5,scale:1}}],metrics:{p95:"tail"}}],{url:"http://metrics",kind:"prometheus"});
    await collectMetrics(value,[source(async()=>({data:{resultType:"vector",result:[1,"+Inf"].map((le,index)=>({metric:{le:String(le)},value:[1001,String(counts[index])]}))},warnings:[],infos:[]}))]);
    expect(buildCallgraph(value)!.arms[0]!.windows[0]!.nodes[0]!.properties.perf.metrics.p95!).toMatchObject({status,value:expected});
    if (status === "missing") {
      expect(buildCallgraph(value)!.arms[0]!.windows[0]!.nodes[0]!.properties.perf.metrics.p95!.reason).toBe("no_observations");
    }
    const html = renderPerfReport(value);
    const display = { observed: expected === "+Inf" ? "N/A" : "1 s", error: "error", missing: "N/A" }[status];
    expect(html).toContain("chat\\nQPS N/A\\np95 " + display + "\\nerr N/A");
    expect(html).not.toContain("Histogram contains zero observed events");
    expect(html).not.toContain("p95 +Inf");
  }
});

test("empty responses cannot conceal warnings or unrelated errors in saved evidence", async () => {
  const value=unplanned();
  planMetrics(value,[probe],{url:"http://metrics",kind:"prometheus"});
  await collectMetrics(value,[source(async()=>({data:{resultType:"vector",result:[]},warnings:[],infos:[]}))]);
  const query=value.executions[0]!.prometheus_observations![0]!;
  for (const error of [null,"ValueError: Prometheus query returned no samples","query cancelled"]) {
    query.error=error;
    expect(buildCallgraph(value)!.arms[0]!.windows[0]!.nodes[0]!.properties.perf.metrics.qps!).toMatchObject({
      status:error === "query cancelled" ? "error" : "missing", reason:error === "query cancelled" ? "query_error" : "empty_result",
    });
  }
  query.error=null;query.warnings=["partial result"];
  expect(renderPerfReport(value)).not.toContain("partial result");
  expect(buildCallgraph(value)!.arms[0]!.windows[0]!.nodes[0]!.properties.perf.metrics.qps!.reason).toBe("query_error");
});

test("bounded collection cancels queued queries and persists partial attempts", async () => {
  const path=directory(), value=unplanned();
  planMetrics(value,[probe,{...probe,target:"operation:mock/search"}],{url:"http://metrics",kind:"prometheus"});
  writeRunData(value,path);
  const controller=new AbortController();let calls=0;
  await expect(recollectMetrics(path,[source(async()=>{calls++;controller.abort(new Error("stop"));return scalar("2");})],{concurrency:1,signal:controller.signal})).rejects.toThrow("stop");
  expect(calls).toBe(1);
  expect(loadRun(path).executions[0]!.prometheus_observations![1]!.error).toBe("collection not completed");
  expect(readdirSync(join(path,"metric-collections"))).toHaveLength(2);
});

test("multi-Case Engine records a Unix origin after setup and restores SSE operation identity", async () => {
  const {SSEOperation} = await import("@compforge/harness-common");
  const service=run().executions[0]!.operation_runs[0]!.service;
  let afterSetup=0;
  const value=await new Engine({service,caseSet:{caseset:"mix",cases:[{id:"a",input:{}},{id:"b",input:{}}]},
    caseMix:[{id:"a",weight:1},{id:"b",weight:2}],runner:{name:"chat",
      setup:async()=>{await new Promise(resolve=>setTimeout(resolve,20));afterSetup=Date.now()/1000;},
      operation:()=>new SSEOperation({name:"chat",serviceName:service.name,method:"POST",path:"/chat"}),
      fire:async()=>{await new Promise(resolve=>setTimeout(resolve,1));return {status:200,duration_ms:1};}},
    loads:[{request_rate:Infinity,max_inflight:1,hold_s:0.04,cooldown_timeout_s:1}]}).run();
  expect(value.case_ids).toEqual(["a","b"]);
  expect(value.executions[0]!.started_at_s!).toBeGreaterThanOrEqual(afterSetup-0.002);
  const path=directory();writeRunData(value,path);
  const operation=loadRun(path).executions[0]!.operation_runs[0]!.operation;
  expect(operation).toBeInstanceOf(SSEOperation);
  expect(operation.id()).toBe(`operation:${service.name}/chat`);
});

test("offline HTML embeds graph libraries, keeps aggregate scope and escapes untrusted labels", () => {
  const value=run();value.experiment="</script><script>evil()</script>";
  const html=renderPerfReport(value);
  expect(html).toContain("Aggregate case scope");
  expect(html).toContain("rkGraph");expect(html).toContain("graph-layout-status");
  expect(html).not.toContain('<script src=');
  expect(html).not.toContain('<script>evil()</script>');
  const paths=writePerfReport(value,directory());
  expect(JSON.parse(readFileSync(paths.callgraph!,"utf8"))).toEqual(buildCallgraph(value));
});

test("independent producers merge disjoint roles and reject conflicting family definitions", () => {
  const value=unplanned();
  const tail: PrometheusProbe={...probe,summaries:[{name:"tail",unit:"s",promql:"histogram"}],metrics:{p95:"tail"}};
  planMetrics(value,[probe,tail],{url:"http://metrics",kind:"prometheus"});
  expect(Object.keys(value.observation_bindings![0]!.metrics)).toEqual(["qps","p95"]);
  const queries = value.executions[0]!.prometheus_observations!;
  expect(queries.filter(q => q.step_s === null)).toHaveLength(2);
  expect(queries.filter(q => q.step_s !== null)).toHaveLength(2);
  expect(()=>planMetrics(unplanned(),[probe,{...tail,summaries:[{name:"qps",unit:"s",promql:"histogram"}],metrics:{p95:"qps"}}],
    {url:"http://metrics",kind:"prometheus"})).toThrow("family");
});

test("Case entry trends share range queries, preserve single points, and render offline", async () => {
  const value = unplanned();
  value.entry_ids = [probe.target, "operation:mock/search"];
  value.case_entries = {greet:probe.target, calculate:probe.target, search:"operation:mock/search"};
  planMetrics(value,[probe],{url:"http://metrics",kind:"prometheus"});
  const client = source(async () => scalar("4"));
  let ranges = 0;
  client.queryRange = async (expression,start) => {
    expect(expression).toContain("[60s]");
    ranges++;
    return {data:{resultType:"matrix",result:[{metric:{},values:[[start,"3"]]}]},warnings:[],infos:[]};
  };
  await collectMetrics(value,[client]);
  expect(ranges).toBe(value.executions.length);
  const dir = directory();
  writeRunData(value,dir);
  const replay = loadRun(dir);
  expect(replay.case_entries).toEqual(value.case_entries);
  const html = renderPerfReport(replay);
  expect(html).toContain("greet、calculate");
  expect(html).toContain("1 个有效点");
  const queries = replay.executions[0]!.prometheus_observations!;
  for (const query of [queries.find(q => q.step_s === null)!, queries.find(q => q.step_s !== null)!]) {
    expect(query.expression).toContain("sum(rate(requests[");
    expect(html).not.toContain(query.expression);
  }
  expect(html).toEqual(renderPerfReport(value));
  client.queryRange = async () => { throw new Error("range unavailable"); };
  await recollectMetrics(dir,[client]);
  expect(renderPerfReport(loadRun(dir))).toContain("查询失败：range unavailable");
  expect(readFileSync(join(dir,"timeseries.csv"),"utf8")).not.toContain("prometheus_query.qps");
});
