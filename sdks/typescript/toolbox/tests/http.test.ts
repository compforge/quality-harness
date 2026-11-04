import { afterEach, expect, test } from "bun:test";
import { createServer, type RequestListener } from "node:http";
import { createServer as createTlsServer } from "node:https";
import { readFileSync } from "node:fs";
import { HttpClient } from "../src/http";
import { ClientManager } from "../src/client-manager";
import { DirectTransport, PortForwardTransport, Transport } from "../src/transport";

const limits = { concurrency: 2, connectTimeoutMs: 500, requestTimeoutMs: 1_000, maxResponseBytes: 1024 };
const cleanup: Array<() => void | Promise<void>> = [];
afterEach(async () => { for (const close of cleanup.splice(0).reverse()) await close(); });
async function server(listener: RequestListener) {
  const instance = createServer(listener);
  await new Promise<void>(resolve => instance.listen(0, "127.0.0.1", resolve));
  const address = instance.address();
  if (!address || typeof address === "string") throw new Error("missing server port");
  cleanup.push(async () => { instance.closeAllConnections(); await new Promise<void>(resolve => instance.close(() => resolve())); });
  return { host: "127.0.0.1", port: address.port, endpoint: `http://127.0.0.1:${address.port}` };
}
function client(endpoint: string, transport: Transport = new DirectTransport(), policy = limits) {
  const http = new HttpClient({ endpoint }, transport, policy);
  cleanup.push(() => http.dispose());
  return http;
}

test("HTTP retains logical Host, escaped URL, method and body through a mapped Transport", async () => {
  const received: unknown[] = [];
  const target = await server(async (req, res) => {
    const chunks = []; for await (const chunk of req) chunks.push(chunk);
    received.push([req.headers.host, req.url, req.method, Buffer.concat(chunks).toString()]);
    res.setHeader("x-response", "kept"); res.end("ok");
  });
  const http = client("http://only-in-cluster.invalid:9000", new PortForwardTransport(async () => target));
  const response = await http.request("/a%2Fb?q=x%2By&q=z", { method: "POST", body: "payload" });
  expect(received).toEqual([["only-in-cluster.invalid:9000", "/a%2Fb?q=x%2By&q=z", "POST", "payload"]]);
  expect(response.status).toBe(200); expect(response.headers["x-response"]).toBe("kept");
  expect(Buffer.from(response.body).toString()).toBe("ok");
});

test("HTTP statuses and redirects are returned without replay or following another origin", async () => {
  let calls = 0;
  const target = await server((req, res) => { calls++; res.statusCode = Number(req.url!.slice(1)); res.setHeader("location", "http://elsewhere.invalid"); res.end(); });
  const http = client(target.endpoint);
  for (const status of [302, 401, 403, 404, 503]) expect((await http.request(`/${status}`)).status).toBe(status);
  expect(calls).toBe(5);
  expect(() => http.request("http://elsewhere.invalid")).toThrow("origin");
  expect(() => http.request("/", { headers: { Host: "elsewhere" } })).toThrow("Host");
});

test("HTTP body overflow closes the response and releases the next pool slot", async () => {
  const target = await server((req, res) => {
    if (req.url === "/large") { res.write("12345"); return; }
    res.end("ok");
  });
  const http = client(target.endpoint, new DirectTransport(), { ...limits, concurrency: 1, maxResponseBytes: 4 });
  await expect(http.request("/large")).rejects.toMatchObject({ kind: "limit_exceeded" });
  expect(Buffer.from((await http.request("/small")).body).toString()).toBe("ok");
});

test("HTTP deadlines cover body reads and cancellation of queued work never sends it", async () => {
  let received!: () => void, calls = 0;
  const started = new Promise<void>(resolve => { received = resolve; });
  const target = await server((_req, res) => { calls++; res.write("x"); received(); });
  const http = client(target.endpoint, new DirectTransport(), { ...limits, concurrency: 1, requestTimeoutMs: 120 });
  const first = http.request("/").catch(error => error);
  await started;
  const abort = new AbortController(); const reason = new Error("queued caller cancelled");
  const queued = http.request("/queued", { signal: abort.signal }); abort.abort(reason);
  await expect(queued).rejects.toBe(reason);
  expect(await first).toMatchObject({ kind: "timeout" }); expect(calls).toBe(1);
});

test("HTTP active cancellation preserves its reason and dispose waits for active and queued requests", async () => {
  let received!: () => void;
  const started = new Promise<void>(resolve => { received = resolve; });
  const target = await server((_req, res) => { res.write("x"); received(); });
  const http = client(target.endpoint, new DirectTransport(), { ...limits, concurrency: 1 });
  const abort = new AbortController(); const reason = new Error("active caller cancelled");
  const first = http.request("/", { signal: abort.signal }); await started; abort.abort(reason);
  await expect(first).rejects.toBe(reason);
  const nextStarted = new Promise<void>(resolve => { received = resolve; });
  const active = http.request("/").catch(error => error);
  await nextStarted;
  const queued = http.request("/queued").catch(error => error);
  await Promise.all([http.dispose(), http.dispose()]);
  expect(await active).toBeInstanceOf(Error); expect(await queued).toBeInstanceOf(Error);
});

test("HTTP clients borrow a shared Transport until root disposal", async () => {
  const target = await server((_req, res) => res.end("ok"));
  let released = 0;
  const manager = new ClientManager(); cleanup.push(() => manager.dispose());
  const route = await manager.get({ clientKey: "route", createClient: () => new PortForwardTransport(async () => target, () => { released++; }) });
  const factory = { createClient: (_clients: unknown, signal: AbortSignal) => new HttpClient({ endpoint: "http://cluster.invalid" }, route, limits, { signal }) };
  const first = await manager.create(factory); const second = await manager.create(factory);
  await first.dispose(); expect(released).toBe(0);
  expect((await second.request("/")).status).toBe(200);
  await manager.dispose(); expect(released).toBe(1);
});

test("HTTP cancellation during route preparation neither sends a request nor closes the borrowed route", async () => {
  let complete!: (value: { host: string; port: number }) => void, sent = 0, released = 0;
  const target = await server((_req, res) => { sent++; res.end(); });
  const route = new PortForwardTransport(() => new Promise(resolve => { complete = resolve; }), () => { released++; });
  const http = client("http://cluster.invalid", route);
  const pending = http.initialize().catch(error => error);
  // Let acquire publish the asynchronous mapping before cancellation.
  await new Promise(resolve => setImmediate(resolve));
  await http.dispose();
  complete(target);
  expect(await pending).toBeInstanceOf(Error); expect(sent).toBe(0); expect(released).toBe(0);
  await route.dispose(); expect(released).toBe(1);
});

test("HTTP TLS forwarding verifies the original certificate identity", async () => {
  const pem = (name: string) => readFileSync(new URL(`./fixtures/pod-log-tls/${name}.pem`, import.meta.url), "utf8");
  const instance = createTlsServer({ key: pem("server-key"), cert: pem("server-cert") }, (req, res) => { expect(req.headers.host).toBe("localhost:9000"); res.end("ok"); });
  await new Promise<void>(resolve => instance.listen(0, "127.0.0.1", resolve));
  const address = instance.address(); if (!address || typeof address === "string") throw new Error("missing port");
  cleanup.push(async () => { instance.closeAllConnections(); await new Promise<void>(resolve => instance.close(() => resolve())); });
  const route = new PortForwardTransport(async () => ({ host: "127.0.0.1", port: address.port }));
  for (const [endpoint, ca, valid] of [["https://localhost:9000", pem("ca"), true], ["https://localhost:9000", undefined, false], ["https://wrong.invalid:9000", pem("ca"), false]] as const) {
    const http = new HttpClient({ endpoint, ca }, route, limits); cleanup.push(() => http.dispose());
    if (valid) expect((await http.request("/")).status).toBe(200);
    else await expect(http.request("/")).rejects.toThrow();
  }
});
