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
