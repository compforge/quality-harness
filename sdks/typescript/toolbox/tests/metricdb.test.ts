import { ToolboxError } from "../src/errors";
import { readFileSync } from "node:fs";
import { ScrapeConfig, ScrapeTarget } from "@compforge/harness-common";
import { afterEach, expect, test } from "bun:test";
import { createServer } from "node:http";
import { HttpClient } from "../src/http";
import { DirectTransport } from "../src/transport";
import { MetricDBClient } from "../src/metricdb";
const cleanup: Array<()=>Promise<void>>=[];
afterEach(async()=>{for (const close of cleanup.splice(0).reverse()) await close();});

test("Prometheus queries retain base path, credentials stay in access, bounds and warnings survive", async()=>{
  const seen:{url:URL;auth:string|undefined}[]=[];
  const server=createServer((req,res)=>{
    seen.push({url:new URL(req.url!,"http://local"),auth:req.headers.authorization});
    res.end(JSON.stringify({status:"success",data:{resultType:"vector",result:[]},warnings:["partial"],infos:["notice"]}));
  });
  await new Promise<void>(resolve=>server.listen(0,"127.0.0.1",resolve));
  cleanup.push(async()=>{server.closeAllConnections();await new Promise<void>(resolve=>server.close(()=>resolve()));});
  const address=server.address();if (!address || typeof address==="string") throw new Error("port missing");
  const url=`http://127.0.0.1:${address.port}/select/0/prometheus`;
  const transport=new DirectTransport();
  const http=new HttpClient({endpoint:url},transport,{concurrency:2,connectTimeoutMs:500,requestTimeoutMs:1000,maxResponseBytes:4096});
  cleanup.push(()=>http.dispose());
  const client=new MetricDBClient(http,{url,kind:"victoriametrics",headers:{authorization:"Bearer test"}});
  expect((await client.query('rate(requests{path="/x"}[1m])',1000.999)).warnings).toEqual(["partial"]);
  await client.queryRange("sum(active)",1000,1002,0.5);
  expect(seen[0]!.url.pathname).toBe("/select/0/prometheus/api/v1/query");
  expect(seen[0]!.url.searchParams.get("time")).toBe("1000.999");
  expect(seen[0]!.url.searchParams.get("latency_offset")).toBe("1ms");
  expect(seen[0]!.url.searchParams.get("nocache")).toBe("1");
  expect(seen[0]!.auth).toBe("Bearer test");
  expect(seen[1]!.url.searchParams.get("step")).toBe("0.5");
  expect(seen[1]!.url.searchParams.get("latency_offset")).toBe("1ms");
  expect(seen[1]!.url.searchParams.get("nocache")).toBe("1");
  expect(client.url).not.toContain("Bearer");
});

test("Prometheus adapter rejects redirects, bad JSON, error envelopes and credential-bearing source URLs", async()=>{
  const body=(value:unknown)=>new TextEncoder().encode(JSON.stringify(value));
  for (const response of [{status:302,headers:{},body:body({secret:"hidden"})},
    {status:200,headers:{},body:new TextEncoder().encode("<html>secret</html>")},
    {status:200,headers:{},body:body({status:"error",error:"sensitive backend details"})}]) {
    const client=new MetricDBClient({request:async()=>response},{url:"http://metrics"});
    try {await client.query("up",1);throw new Error("unexpected success");}
    catch(error) {expect(String(error)).not.toMatch(/secret|sensitive/);expect(String(error)).toContain("MetricDB");}
  }
  expect(()=>new MetricDBClient({request:async()=>{throw new Error();}},{url:"http://user:secret@metrics"})).toThrow("credentials");
});

/** @case Backend selection is independent of stored application metrics and keeps access failures distinct. */
test("connect selects query dialect without assuming metrics have been scraped", async () => {
  for (const kind of ["prometheus", "victoriametrics"] as const) {
    let calls = 0;
    const client = await MetricDBClient.connect({request: async (url, options) => {
      calls++;
      const request = new URL(url);
      expect(request.pathname).toBe("/tenant/api/v1/query");
      expect(request.searchParams.get("query")).toBe("increase_prometheus(vector(0)[1m:1m])");
      expect(options?.headers?.authorization).toBe("Bearer fixture");
      return {status: kind === "prometheus" ? 400 : 200, headers: {}, body: new TextEncoder().encode(JSON.stringify(
        kind === "prometheus" ? {status:"error",errorType:"bad_data",error:'parse error: unknown function with name "increase_prometheus"'}
          : {status:"success",data:{resultType:"vector",result:[]}}))};
    }}, {url:"http://metrics/tenant",headers:{authorization:"Bearer fixture"}});
    expect(client.kind).toBe(kind);
    expect(calls).toBe(1);
  }
});

test("backend detection never falls back on denied, invalid or unavailable responses", async () => {
  for (const [status, payload] of [
    [401,{status:"error",error:"secret"}], [403,{status:"error",error:"secret"}],
    [503,{status:"error",error:"secret"}], [302,{location:"secret"}],
    [400,{status:"error",errorType:"bad_data",error:"query blocked: secret"}],
    [200,{status:"error",error:"secret"}], [200,{status:"success",data:{resultType:"scalar",result:[1,"1"]}}],
  ] as const) {
    await expect(MetricDBClient.connect({request: async () => ({status,headers:{},
      body:new TextEncoder().encode(JSON.stringify(payload))})},{url:"http://metrics"})).rejects.toThrow();
  }
  const controller = new AbortController(), reason = new Error("cancelled");
  controller.abort(reason);
  await expect(MetricDBClient.connect({request: async () => {throw new Error("must not request");}},
    {url:"http://metrics"},controller.signal)).rejects.toBe(reason);
});

test("partial backend responses remain explicit evidence even when samples exist", async () => {
  const client = new MetricDBClient({request: async () => ({status:200,headers:{},
    body:new TextEncoder().encode(JSON.stringify({status:"success",isPartial:true,
      data:{resultType:"vector",result:[{metric:{},value:[1,"2"]}]}}))})},{url:"http://metrics"});
  const result = await client.query("requests",1);
  expect(result.warnings).toContain("Metric backend returned a partial result");
  expect(result.data.result).toHaveLength(1);
});

/** @case id=frozen_vm_timestamp,desc=`Recent VM evaluation`,expect=`Requested time is not silently shifted; Prometheus gets no VM parameters` */
test("only VM requests disable evaluation latency for instant and range queries", async () => {
  for (const kind of ["prometheus","victoriametrics"] as const) {
    const seen: URL[] = [];
    const client = new MetricDBClient({request: async url => {
      seen.push(new URL(url));
      return {status:200,headers:{},body:new TextEncoder().encode(JSON.stringify({status:"success",data:{resultType:"vector",result:[]}}))};
    }}, {url:"http://metrics/prefix",kind});
    const end = Date.now()/1000;
    await client.query("time()",end);
    await client.queryRange("time()",end-60,end,1);
    expect(seen[0]!.searchParams.get("time")).toBe(String(end));
    expect(seen[1]!.searchParams.get("end")).toBe(String(end));
    for (const request of seen) {
      expect(request.searchParams.get("latency_offset")).toBe(kind === "victoriametrics" ? "1ms" : null);
      expect(request.searchParams.get("nocache")).toBe(kind === "victoriametrics" ? "1" : null);
    }
  }
});

/** @case Partial flags retain all samples but never imply complete evidence. */
test("MetricDB preserves explicit completeness and rejects malformed flags", async () => {
  for (const kind of ["prometheus", "victoriametrics"] as const) {
    for (const partial of [undefined, false, true, "true", null]) {
      const client = new MetricDBClient({request: async () => ({status:200,headers:{},
        body:new TextEncoder().encode(JSON.stringify({status:"success",isPartial:partial,
          data:{resultType:"vector",result:[{metric:{},value:[123.456,"2"]}]},
          warnings:["server warning"],infos:["server info"]}))})},{url:"http://metrics",kind});
      if (partial === null || typeof partial === "string") {
        await expect(client.query("requests",123.456)).rejects.toThrow("partial");
      } else {
        const result = await client.query("requests",123.456);
        expect(result.isPartial).toBe(partial);
        expect(result.data.result).toEqual([{metric:{},value:[123.456,"2"]}]);
        expect(result.infos).toEqual(["server info"]);
        expect(result.warnings).toEqual(partial ? ["server warning","Metric backend returned a partial result"] : ["server warning"]);
      }
    }
  }
});

test("collector reads return common scrape models through the supplied route", async () => {
  const fixture = JSON.parse(readFileSync(new URL("../../../../conformance/metricdb/scrape.json", import.meta.url), "utf8"));
  const calls: string[] = [];
  const signal = new AbortController().signal;
  const client = new MetricDBClient({ request: async (url, options) => {
    calls.push(url);
    expect(options?.headers?.authorization).toBe("collector-token");
    expect(options?.signal).toBe(signal);
    const data = url.endsWith("/status/config") ? fixture.config : fixture.targets;
    return { status: 200, headers: {}, body: Buffer.from(JSON.stringify({status: "success", data})) };
  } }, {url: "http://collector/proxy/", kind: "victoriametrics", headers: {authorization: "collector-token"}});
  const jobs = await client.getScrapeConfig(signal);
  expect(jobs[0]).toBeInstanceOf(ScrapeConfig);
  expect(jobs[0]).toMatchObject({jobName: "gateway", scrapeInterval: "10s", scrapeTimeout: "8s", metricsPath: "/internal/metrics", scheme: "https"});
  expect(jobs[1]).toMatchObject({jobName: "worker", scrapeInterval: "30s"});
  const targets = await client.listScrapeTargets({}, signal);
  expect(targets.every(target => target instanceof ScrapeTarget)).toBe(true);
  expect(targets[0]).toMatchObject({...fixture.targets.activeTargets[0], state: "active"});
  expect(targets[1]!.scrapeInterval).toBeUndefined();
  expect(targets[2]).toMatchObject({...fixture.targets.droppedTargets[0], state: "dropped"});
  expect(await client.listScrapeTargets({state: "dropped"}, signal)).toHaveLength(1);
  expect(calls).toEqual(["status/config","targets?state=any","targets?state=dropped"].map(path => "http://collector/proxy/api/v1/"+path));
});

test("empty collector results remain distinct from invalid data and HTTP failures", async () => {
  function collector(status: number, data: unknown) {
    return new MetricDBClient({request: async () => ({status, headers: {},
      body: Buffer.from(JSON.stringify({status: "success", data}))})}, {url: "http://collector"});
  }
  expect(await collector(200, {yaml:"scrape_configs: []"}).getScrapeConfig()).toEqual([]);
  expect(await collector(200, {activeTargets:[]}).listScrapeTargets({state:"active"})).toEqual([]);
  await expect(collector(403, {}).getScrapeConfig()).rejects.toMatchObject({code:403,kind:"permission_denied"});
  await expect(collector(404, {}).listScrapeTargets()).rejects.toMatchObject({code:404});
  await expect(collector(200, {yaml:"credentials: ["}).getScrapeConfig()).rejects.toMatchObject({kind:"invalid_response"});
  await expect(collector(200, {droppedTargets:[]}).listScrapeTargets()).rejects.toMatchObject({kind:"invalid_response"});
});

test("both scrape reads classify transport failures and deadlines, and preserve caller cancellation", async () => {
  const cause = new Error("private transport diagnostics");
  const collector = new MetricDBClient({request: async () => { throw cause; }}, {url: "http://collector"});
  for (const read of [(signal?: AbortSignal) => collector.getScrapeConfig(signal),
    (signal?: AbortSignal) => collector.listScrapeTargets({}, signal)]) {
    const error = await read().catch(error => error);
    expect(error).toBeInstanceOf(ToolboxError);
    expect(error).toMatchObject({kind: "connection_failed", cause});
    expect(error.message).not.toContain("private");
    const timeout = AbortSignal.abort(new DOMException("deadline exceeded", "TimeoutError"));
    await expect(read(timeout)).rejects.toMatchObject({kind: "timeout", cause: timeout.reason});
    const cancelled = AbortSignal.abort(new Error("stop"));
    await expect(read(cancelled)).rejects.toBe(cancelled.reason);
  }
  // @ts-expect-error Runtime callers can supply an invalid target filter.
  await expect(collector.listScrapeTargets({state: "unknown"})).rejects.toMatchObject({kind: "invalid_argument"});
});

test("split endpoints isolate credentials and preserve API prefixes, even on one origin", async () => {
  const seen: [string, string | undefined][] = [];
  const http = {request: async (url: string, options?: {headers?: Record<string,string>}) => {
    seen.push([url, options?.headers?.Authorization]);
    return {status:200, headers:{}, body:Buffer.from(JSON.stringify({status:"success", data:{
      resultType:"vector", result:[], activeTargets:[], droppedTargets:[], yaml:"scrape_configs: []",
    }}))};
  }};
  const query = {url:"http://gateway/select/0/prometheus", username:"query", password:"q"};
  for (const collector of [undefined, {url:"http://gateway/agent"}, {url:"http://agent", username:"agent", password:"a"}]) {
    seen.length = 0;
    const client = await MetricDBClient.connect(http, {...query, collector});
    await client.queryRange("up", 1, 2, 1);
    await client.getScrapeConfig();
    await client.listScrapeTargets({state:"active"});
    const endpoint = collector ?? query;
    const auth = (pair: string) => "Basic " + Buffer.from(pair).toString("base64");
    expect(seen.slice(0,2).map(([url, headers]) => [new URL(url).pathname, headers])).toEqual([
      ["/select/0/prometheus/api/v1/query", auth("query:q")], ["/select/0/prometheus/api/v1/query_range", auth("query:q")],
    ]);
    expect(seen.slice(2)).toEqual(["status/config","targets?state=active"].map(path =>
      [endpoint.url + "/api/v1/" + path, collector ? collector.username ? auth("agent:a") : undefined : auth("query:q")]));
  }
});
