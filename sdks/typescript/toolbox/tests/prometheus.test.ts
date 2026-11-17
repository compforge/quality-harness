import { afterEach, expect, test } from "bun:test";
import { createServer } from "node:http";
import { HttpClient } from "../src/http";
import { DirectTransport } from "../src/transport";
import { PrometheusClient } from "../src/prometheus";
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
  const client=new PrometheusClient(http,{url,kind:"victoriametrics",headers:{authorization:"Bearer test"}});
  expect((await client.query('rate(requests{path="/x"}[1m])',1000.999)).warnings).toEqual(["partial"]);
  await client.queryRange("sum(active)",1000,1002,0.5);
  expect(seen[0]!.url.pathname).toBe("/select/0/prometheus/api/v1/query");
  expect(seen[0]!.url.searchParams.get("time")).toBe("1000.999");
  expect(seen[0]!.auth).toBe("Bearer test");
  expect(seen[1]!.url.searchParams.get("step")).toBe("0.5");
  expect(client.url).not.toContain("Bearer");
});

test("Prometheus adapter rejects redirects, bad JSON, error envelopes and credential-bearing source URLs", async()=>{
  const body=(value:unknown)=>new TextEncoder().encode(JSON.stringify(value));
  for (const response of [{status:302,headers:{},body:body({secret:"hidden"})},
    {status:200,headers:{},body:new TextEncoder().encode("<html>secret</html>")},
    {status:200,headers:{},body:body({status:"error",error:"sensitive backend details"})}]) {
    const client=new PrometheusClient({request:async()=>response},{url:"http://metrics"});
    try {await client.query("up",1);throw new Error("unexpected success");}
    catch(error) {expect(String(error)).not.toMatch(/secret|sensitive/);expect(String(error)).toContain("Prometheus");}
  }
  expect(()=>new PrometheusClient({request:async()=>{throw new Error();}},{url:"http://user:secret@metrics"})).toThrow("credentials");
});

/** @case Backend selection is independent of stored application metrics and keeps access failures distinct. */
test("connect selects query dialect without assuming metrics have been scraped", async () => {
  for (const kind of ["prometheus", "victoriametrics"] as const) {
    let calls = 0;
    const client = await PrometheusClient.connect({request: async (url, options) => {
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
    await expect(PrometheusClient.connect({request: async () => ({status,headers:{},
      body:new TextEncoder().encode(JSON.stringify(payload))})},{url:"http://metrics"})).rejects.toThrow();
  }
  const controller = new AbortController(), reason = new Error("cancelled");
  controller.abort(reason);
  await expect(PrometheusClient.connect({request: async () => {throw new Error("must not request");}},
    {url:"http://metrics"},controller.signal)).rejects.toBe(reason);
});

test("partial backend responses remain explicit evidence even when samples exist", async () => {
  const client = new PrometheusClient({request: async () => ({status:200,headers:{},
    body:new TextEncoder().encode(JSON.stringify({status:"success",isPartial:true,
      data:{resultType:"vector",result:[{metric:{},value:[1,"2"]}]}}))})},{url:"http://metrics"});
  const result = await client.query("requests",1);
  expect(result.warnings).toContain("Metric backend returned a partial result");
  expect(result.data.result).toHaveLength(1);
});
