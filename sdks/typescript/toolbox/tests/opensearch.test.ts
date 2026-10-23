import { expect, test } from "bun:test";
import { createServer, type IncomingMessage, type ServerResponse } from "node:http";
import { spawn } from "node:child_process";
import { fileURLToPath } from "node:url";
import { OpenSearchClient, openOpenSearch } from "../src/opensearch";
import { DirectTransport, type TcpTransport } from "../src/transport";

async function serve(handler: (request: IncomingMessage, response: ServerResponse) => void) {
  const server = createServer(handler);
  await new Promise<void>(resolve => server.listen(0, "127.0.0.1", resolve));
  const endpoint = { host: "127.0.0.1", port: (server.address() as { port: number }).port };
  return { endpoint, close: async () => {
    server.closeAllConnections();
    await new Promise<void>(resolve => server.close(() => resolve()));
  } };
}

test("OpenSearch probes real driver connections, falls back, and reuses the selected route", async () => {
  const requests: string[] = [];
  const server = await serve((req, res) => {
    requests.push(`${req.method} ${req.url}`);
    expect(req.headers.authorization).toBe(`Basic ${Buffer.from("reader:secret").toString("base64")}`);
    res.setHeader("content-type", "application/json");
    res.end(JSON.stringify(req.url?.endsWith("_count") ? { count: 12 } : { hits: { hits: [] } }));
  });
  const routes: unknown[] = [];
  let connects = 0;
  const fallback: TcpTransport = { kind: "tcp", name: "pod-relay", connect: async () => { connects++; return server.endpoint; } };
  const client = new OpenSearchClient({
    resolve: async () => ({ node: "http://unreachable.invalid:9200", auth: { username: "reader", password: "secret" }, requestTimeoutMs: 500 }),
    transports: [new DirectTransport(), fallback],
  }, { onRoute: route => routes.push(route) });
  try {
    await Promise.all([client.initialize(), client.initialize()]);
    expect(await client.count("spans", { match_all: {} })).toBe(12);
    await client.search("spans", { query: { match_all: {} } });
    expect(connects).toBe(1);
    expect(routes).toEqual([{ transport: "pod-relay", reason: "ConnectionError" }]);
    expect(requests).toEqual(["HEAD /", "POST /spans/_count", "POST /spans/_search"]);
  } finally { await client.dispose(); await server.close(); }
});

test.each([401, 403, 500])("OpenSearch HTTP %s during preparation does not change route", async status => {
  const server = await serve((_req, res) => { res.writeHead(status); res.end(); });
  let fallback = 0;
  try {
    await expect(openOpenSearch({
      resolve: async () => ({ node: `http://127.0.0.1:${server.endpoint.port}`, auth: {} }),
      transports: [new DirectTransport(), { kind: "tcp", name: "unexpected", connect: async endpoint => { fallback++; return endpoint; } }],
    })).rejects.toMatchObject({ statusCode: status });
    expect(fallback).toBe(0);
  } finally { await server.close(); }
});

test.each(["count", "search", "request"] as const)("OpenSearch %s disconnect is not replayed", async operation => {
  let reads = 0;
  let fallback = 0;
  const server = await serve((req, res) => {
    if (req.method === "HEAD") res.end();
    else { reads++; req.socket.destroy(); }
  });
  const client = await openOpenSearch({
    resolve: async () => ({ node: `http://127.0.0.1:${server.endpoint.port}`, auth: {}, requestTimeoutMs: 500 }),
    transports: [new DirectTransport(), { kind: "tcp", name: "unexpected", connect: async endpoint => { fallback++; return endpoint; } }],
  });
  try {
    const pending = operation === "request" ? client.request("/_cat/indices") : client[operation]("spans", {});
    await expect(pending).rejects.toThrow();
    expect(reads).toBe(1);
    expect(fallback).toBe(0);
  } finally { await client.dispose(); await server.close(); }
});

test("OpenSearch exhausted routes retain a safe connection phase and the driver cause", async () => {
  let attempts = 0;
  const route: TcpTransport = { kind: "tcp", name: "failed", connect: async () => {
    attempts++;
    throw Object.assign(new Error("private endpoint"), { code: "ENOTFOUND" });
  } };
  const error = await openOpenSearch({ resolve: async () => ({ node: "http://search:9200", auth: {} }), transports: [route, route] }).catch(error => error);
  expect(attempts).toBe(2);
  expect(error).toMatchObject({ kind: "connection_failed", code: "ENOTFOUND", cause: { code: "ENOTFOUND" } });
  expect(error.message).toBe("OpenSearch connect failed (ENOTFOUND)");
});

test("OpenSearch disposal aborts an in-flight probe without starting another route", async () => {
  let started!: () => void;
  const ready = new Promise<void>(resolve => { started = resolve; });
  const server = await serve(() => started());
  let fallback = 0;
  const client = new OpenSearchClient({
    resolve: async () => ({ node: `http://127.0.0.1:${server.endpoint.port}`, auth: {}, requestTimeoutMs: 30_000 }),
    transports: [new DirectTransport(), { kind: "tcp", name: "unexpected", connect: async endpoint => { fallback++; return endpoint; } }],
  });
  const pending = client.initialize().catch(error => error);
  try {
    await ready;
    const closing = client.dispose();
    expect(client.dispose()).toBe(closing);
    await closing;
    expect(await pending).toBeInstanceOf(Error);
    expect(fallback).toBe(0);
  } finally { await client.dispose(); await server.close(); }
});

test("OpenSearch routed HTTPS preserves TLS identity against a Node server", async () => {
  const fixtures = fileURLToPath(new URL("./fixtures/pod-log-tls/", import.meta.url));
  const server = spawn("node", ["--input-type=module", "-e", `
    import { createServer } from 'node:https';
    import { readFileSync } from 'node:fs';
    const dir = ${JSON.stringify(fixtures)};
    const server = createServer({key: readFileSync(dir+'server-key.pem'), cert: readFileSync(dir+'server-cert.pem')}, (req,res) => {
      console.log('sni='+req.socket.servername); res.end();
    });
    server.listen(0,'127.0.0.1',()=>console.log(server.address().port));
  `], { stdio: ["ignore", "pipe", "pipe"] });
  let output = "";
  server.stdout.on("data", chunk => { output += chunk; });
  const port = await new Promise<number>((resolve, reject) => {
    server.stdout.once("data", chunk => resolve(Number(String(chunk).trim())));
    server.once("error", reject);
    server.once("exit", () => reject(new Error("TLS fixture exited")));
  });
  const client = new OpenSearchClient({
    resolve: async () => ({ node: "https://search.internal:9200", auth: {}, requestTimeoutMs: 1000 }),
    transports: [{ kind: "tcp", name: "pod-relay", connect: async () => ({ host: "127.0.0.1", port }) }],
  });
  try { await client.initialize(); expect(output).toContain("sni=search.internal"); }
  finally { await client.dispose(); server.kill(); await new Promise<void>(resolve => server.once("exit", () => resolve())); }
});

test("OpenSearch probe timeout advances to the next route", async () => {
  const stalled = await serve(() => {});
  const healthy = await serve((_req, res) => res.end());
  const routes: unknown[] = [];
  let client: OpenSearchClient | undefined;
  try {
    client = await openOpenSearch({
      resolve: async () => ({ node: `http://127.0.0.1:${stalled.endpoint.port}`, auth: {}, connectTimeoutMs: 30 }),
      transports: [new DirectTransport(), { kind: "tcp", name: "pod-relay", connect: async () => healthy.endpoint }],
    }, { onRoute: route => routes.push(route) });
    expect(routes).toEqual([{ transport: "pod-relay", reason: "ETIMEDOUT" }]);
  } finally { await client?.close(); await stalled.close(); await healthy.close(); }
});

test("OpenSearch connect deadline does not shorten a subsequent query deadline", async () => {
  const server = await serve((req, res) => {
    if (req.method === "HEAD") res.end();
    else setTimeout(() => { res.setHeader("content-type", "application/json"); res.end('{"count":12}'); }, 80);
  });
  let client: OpenSearchClient | undefined;
  try {
    client = await openOpenSearch({
      resolve: async () => ({ node: `http://127.0.0.1:${server.endpoint.port}`, auth: {}, connectTimeoutMs: 30, requestTimeoutMs: 500 }),
      transports: [new DirectTransport()],
    });
    expect(await client.count("spans", {})).toBe(12);
  } finally { await client?.close(); await server.close(); }
});

test("OpenSearch mapping deadline advances routes and a late mapping cannot start another probe", async () => {
  let release!: () => void;
  const blocked = new Promise<void>(resolve => { release = resolve; });
  let probes = 0;
  const server = await serve((_req, res) => { probes++; res.end(); });
  const routes: unknown[] = [];
  const client = new OpenSearchClient({
    resolve: async () => ({ node: "http://search.internal:9200", auth: {}, connectTimeoutMs: 40, acquireTimeoutMs: 1000 }),
    transports: [
      { kind: "tcp", name: "slow", connect: async () => { await blocked; return server.endpoint; } },
      { kind: "tcp", name: "ready", connect: async () => server.endpoint },
    ],
  }, { onRoute: route => routes.push(route) });
  try {
    await client.initialize();
    release();
    await new Promise<void>(resolve => setImmediate(resolve));
    expect(probes).toBe(1);
    expect(routes).toEqual([{ transport: "ready", reason: "ETIMEDOUT" }]);
  } finally { release(); await client.dispose(); await server.close(); }
});

test.each([
  ["total acquisition", 1000, 20],
  ["last route", 20, 1000],
] as const)("OpenSearch %s deadline retains the shared timeout kind", async (_phase, connectTimeoutMs, acquireTimeoutMs) => {
  await expect(openOpenSearch({
    resolve: async () => ({ node: "http://search.internal:9200", auth: {}, connectTimeoutMs, acquireTimeoutMs }),
    transports: [{ kind: "tcp", name: "blocked", connect: () => new Promise(() => {}) }],
  })).rejects.toMatchObject({ kind: "timeout", code: "ETIMEDOUT" });
});
