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
