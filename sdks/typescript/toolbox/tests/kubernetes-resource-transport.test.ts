import { expect, test, spyOn } from "bun:test";
import { KubeConfig } from "@kubernetes/client-node";
import type { RequestOptions } from "node:https";
import { createServer, type IncomingMessage, type ServerResponse } from "node:http";
import { createServer as createHttpsServer } from "node:https";
import { mkdtempSync, readFileSync, rmSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { KubernetesClient } from "../src/kubernetes/client";
import { KubectlExecutor, type ExecResult } from "../src/kubernetes/executor";




const result = (): ExecResult => ({ ok: true, stdout: "compatibility", stderr: "", exitCode: 0, timedOut: false, durationMs: 0, command: ["kubectl"] });
const tlsFixture = (name: string) => readFileSync(join(import.meta.dir, "fixtures/pod-log-tls", name));

async function fixture(tls = false, useCache?: boolean) {
  const requests: Array<{ path: string; authorization?: string }> = [];
  let connections = 0;
  let fallbacks = 0;
  let respond = (_request: IncomingMessage, response: ServerResponse) => { response.end(JSON.stringify({ items: [] })); };
  const handler = (request: IncomingMessage, response: ServerResponse) => {
    requests.push({ path: request.url!, authorization: request.headers.authorization });
    respond(request, response);
  };
  const server = tls ? createHttpsServer({
    key: tlsFixture("server-key.pem"), cert: tlsFixture("server-cert.pem"), ca: tlsFixture("ca.pem"),
    requestCert: true, rejectUnauthorized: true,
  }, handler) : createServer(handler);
  server.on("connection", () => { connections++; });
  await new Promise<void>(resolve => server.listen(0, resolve));
  const address = server.address();
  if (!address || typeof address === "string") throw new Error("expected fixture port");
  const directory = mkdtempSync(join(tmpdir(), "kubernetes-resource-transport-"));
  const kubeconfig = join(directory, "config.json");
  const user = { token: "fixture-token", ...(tls ? {
    "client-certificate-data": tlsFixture("client-cert.pem").toString("base64"),
    "client-key-data": tlsFixture("client-key.pem").toString("base64"),
  } : {}) };
  writeFileSync(kubeconfig, JSON.stringify({
    apiVersion: "v1", kind: "Config", "current-context": "fixture",
    clusters: [{ name: "fixture", cluster: { server: `${tls ? "https" : "http"}://localhost:${address.port}`,
      ...(tls ? { "certificate-authority-data": tlsFixture("ca.pem").toString("base64") } : {}),
    } }],
    contexts: [{ name: "fixture", context: { cluster: "fixture", user: "fixture" } }], users: [{ name: "fixture", user }],
  }));
  const kube = { namespace: "first", kubeconfig, useCache };
  const executor = new KubectlExecutor(kube);
  executor.run = async () => { fallbacks++; return result(); };
  executor.exec = async () => { fallbacks++; return result(); };
  const client = new KubernetesClient(kube, undefined, executor, { concurrency: 1, maxBytes: 1024, timeoutMs: 1000 });
  await client.initialize();
  return {
    client, requests, kubeconfig, executor,
    get connections() { return connections; }, get fallbacks() { return fallbacks; },
    set respond(handler: typeof respond) { respond = handler; },
    async dispose() {
      await client.dispose();
      server.closeAllConnections();
      await new Promise<void>(resolve => server.close(() => resolve()));
      rmSync(directory, { recursive: true, force: true });
    },
  };
}

const pods = { apiVersion: "v1", resource: "pods" };

for (const tls of [false, true]) {
  test(`resource reads share one ${tls ? "mutually authenticated TLS" : "TCP"} connection across namespace views`, async () => {
    const f = await fixture(tls);
    try {
      const first = f.client.inNamespace("first");
      const second = f.client.inNamespace("second");
      f.respond = (_request, response) => response.end(JSON.stringify({ metadata: { name: "api" }, spec: { ports: [80] } }));
      expect(await first.get({ apiVersion: "v1", resource: "services", name: "api" }))
        .toEqual({ metadata: { name: "api" }, spec: { ports: [80] } });
      f.respond = (_request, response) => response.end(JSON.stringify({ items: [], metadata: { continue: "next-page" } }));
      expect(await second.list({ ...pods, labelSelector: "app=api" }))
        .toEqual({ items: [], metadata: { continue: "next-page" } });
      expect(f.connections).toBe(1); expect(f.fallbacks).toBe(0);
      expect(f.requests).toEqual([
        { path: "/api/v1/namespaces/first/services/api", authorization: "Bearer fixture-token" },
        { path: "/api/v1/namespaces/second/pods?labelSelector=app%3Dapi", authorization: "Bearer fixture-token" },
      ]);
      await first.get({ apiVersion: "apps/v1", resource: "deployments", name: "api" });
      expect(f.requests[2]?.path).toBe("/apis/apps/v1/namespaces/first/deployments/api");
      expect(f.connections).toBe(1);
      await f.client.dispose();
      expect(() => second.list(pods)).toThrow("disposed");
      expect("dispose" in second).toBe(false);
    } finally { await f.dispose(); }
  });
}

test("GVR queries support custom resources, selectors and explicit pages without a resource allowlist", async () => {
  const f = await fixture();
  try {
    await f.client.list({ apiVersion: "example.io/v1alpha1", resource: "widgets", namespace: "other",
      labelSelector: "app=api", fieldSelector: "metadata.name=widget", limit: 10, continue: "page/+=" });
    const url = new URL(f.requests[0]!.path, "http://fixture");
    expect(url.pathname).toBe("/apis/example.io/v1alpha1/namespaces/other/widgets");
    expect(Object.fromEntries(url.searchParams)).toEqual({ labelSelector: "app=api", fieldSelector: "metadata.name=widget", limit: "10", continue: "page/+=" });
    expect(f.requests).toHaveLength(1);
    for (const query of [{ ...pods, apiVersion: "../../v1" }, { ...pods, resource: "pods/status" },
      { ...pods, namespace: "../other" }, { ...pods, limit: 0 }]) {
      await expect(f.client.list(query)).rejects.toMatchObject({ kind: "invalid_argument" });
    }
    await expect(f.client.get({ ...pods, name: "" })).rejects.toMatchObject({ kind: "invalid_argument" });
    expect(f.requests).toHaveLength(1);
  } finally { await f.dispose(); }
});

test("API errors preserve structured kind and HTTP status without shell results or replay", async () => {
  const f = await fixture();
  try {
    for (const [status, kind] of [[401, "authentication_failed"], [403, "permission_denied"],
      [404, "resource_not_found"], [429, "limit_exceeded"], [500, "operation_failed"]] as const) {
      f.respond = (_request, response) => { response.writeHead(status); response.end("private-response-body"); };
      const error = await f.client.get({ apiVersion: "v1", resource: "secrets", name: "credentials" }).catch(error => error);
      expect(error).toMatchObject({ kind, code: status });
      expect(error.message).not.toContain("private-response-body");
      expect(error.message).toContain("/namespaces/first/secrets/credentials");
    }
    expect(f.requests).toHaveLength(5); expect(f.fallbacks).toBe(0);
  } finally { await f.dispose(); }
});

test("deadlines, byte limits, malformed data and caller abort preserve later reads", async () => {
  const f = await fixture();
  try {
    f.respond = () => {};
    await expect(f.client.list(pods, { timeoutMs: 30 })).rejects.toMatchObject({ kind: "timeout" });
    const controller = new AbortController();
    const aborted = f.client.list(pods, { signal: controller.signal });
    setTimeout(() => controller.abort(new Error("cancel request")), 15);
    await expect(aborted).rejects.toThrow("cancel request");
    for (const [body, kind] of [["x".repeat(2048), "limit_exceeded"], ["{", "invalid_response"],
      ["null", "invalid_response"], ['{"items":[null]}', "invalid_response"], ["{}", "invalid_response"]]) {
      f.respond = (_request, response) => response.end(body);
      await expect(f.client.list(pods)).rejects.toMatchObject({ kind });
    }
    f.respond = (_request, response) => response.end(JSON.stringify({ items: [] }));
    expect(await f.client.list(pods)).toEqual({ items: [] });
    f.respond = () => {};
    const active = f.client.list(pods).catch(error => error);
    await new Promise(resolve => setTimeout(resolve, 15));
    await f.client.dispose();
    expect((await active).message).toContain("disposed");
    expect(() => f.client.list(pods)).toThrow("disposed");
    expect(f.fallbacks).toBe(0);
  } finally { await f.dispose(); }
});

test("queued requests expire without authentication or HTTP dispatch", async () => {
  const f = await fixture();
  const controller = new AbortController();
  let started!: () => void;
  const ready = new Promise<void>(resolve => { started = resolve; });
  f.respond = () => { started(); };
  const first = f.client.list(pods, { signal: controller.signal }).catch(error => error);
  try {
    await ready;
    await expect(f.client.list(pods, { timeoutMs: 20 })).rejects.toMatchObject({ kind: "timeout" });
    expect(f.requests).toHaveLength(1);
    controller.abort();
    await first;
    f.respond = (_request, response) => response.end(JSON.stringify({ items: [] }));
    expect(await f.client.list(pods)).toEqual({ items: [] });
  } finally { controller.abort(); await first; await f.dispose(); }
});

test("exec is a separate capability and an injected executor never intercepts resource reads", async () => {
  const f = await fixture();
  try {
    expect(await f.client.list(pods)).toEqual({ items: [] });
    expect((await f.client.exec("first", { pod: "pod", container: "main" }, ["printenv", "-0"])).stdout).toBe("compatibility");
    expect(f.fallbacks).toBe(1); expect(f.requests).toHaveLength(1);
    expect("run" in f.client).toBe(false);
  } finally { await f.dispose(); }
});

test("credential headers refresh on the existing connection; TLS policy changes select a new pool", async () => {
  const original = KubeConfig.prototype.applyToHTTPSOptions;
  let token = "first-token";
  let servername = "first";
  const destroyed: boolean[] = [];
  const auth = spyOn(KubeConfig.prototype, "applyToHTTPSOptions").mockImplementation(async function(this: KubeConfig, options: RequestOptions) {
    this.users = this.users.map(user => ({ ...user, token }));
    await original.call(this, options);
    options.servername = servername;
    const index = destroyed.push(false) - 1;
    const agent = options.agent as import("node:http").Agent;
    const destroy = agent.destroy.bind(agent);
    agent.destroy = () => { destroyed[index] = true; destroy(); };
  });
  const f = await fixture();
  try {
    await f.client.list(pods);
    token = "second-token";
    await f.client.list(pods);
    expect(f.connections).toBe(1);
    expect(f.requests.map(request => request.authorization)).toEqual(["Bearer first-token", "Bearer second-token"]);
    servername = "second";
    await f.client.list(pods);
    expect(destroyed).toEqual([true, true, false]);
  } finally { auth.mockRestore(); await f.dispose(); }
});

test("native reads preserve TLS verification failures and never fall back", async () => {
  const f = await fixture(true);
  try {
    const document = JSON.parse(readFileSync(f.kubeconfig, "utf8"));
    delete document.clusters[0].cluster["certificate-authority-data"];
    writeFileSync(f.kubeconfig, JSON.stringify(document));
    await expect(f.client.list(pods)).rejects.toMatchObject({ kind: "tls_verification_failed" });
    expect(f.fallbacks).toBe(0); expect(f.requests).toHaveLength(0);
  } finally { await f.dispose(); }
});

test("credential preparation observes the deadline and late completion releases its unused Agent", async () => {
  const f = await fixture();
  const original = KubeConfig.prototype.applyToHTTPSOptions;
  let complete!: () => void;
  const prepared = new Promise<void>(resolve => { complete = resolve; });
  let destroyed = false;
  const auth = spyOn(KubeConfig.prototype, "applyToHTTPSOptions").mockImplementation(async function(this: KubeConfig, options: RequestOptions) {
    await original.call(this, options);
    const agent = options.agent as import("node:http").Agent;
    const destroy = agent.destroy.bind(agent);
    agent.destroy = () => { destroyed = true; destroy(); };
    await prepared;
  });
  try {
    await expect(f.client.list(pods, { timeoutMs: 20 })).rejects.toMatchObject({ kind: "timeout" });
    await f.client.dispose();
    complete();
    await new Promise(resolve => setTimeout(resolve, 0));
    expect(destroyed).toBe(true);
    expect(f.requests).toHaveLength(0); expect(f.fallbacks).toBe(0);
  } finally { auth.mockRestore(); complete(); await f.dispose(); }
});

async function until(check: () => boolean | Promise<boolean>) {
  const start = Date.now();
  while (!await check()) {
    if (Date.now() - start > 4000) throw new Error("Timed out waiting for Kubernetes fixture");
    await new Promise(resolve => setTimeout(resolve, 5));
  }
}
const manifest = (name: string, resourceVersion: string) => ({ metadata: { name, resourceVersion, namespace: "first" } });

test("useCache routes unchanged list calls through cold, ready, disconnected and recovered states over mTLS", async () => {
  const f = await fixture(true, true);
  const q = { ...pods, labelSelector: "app=test", fieldSelector: "status.phase=Running" };
  const versions: string[] = [];
  const streams: ServerResponse[] = [];
  let direct = 0;
  let pages = 0;
  let allowWatch = false;
  f.respond = (req, res) => {
    const params = new URL(req.url!, "http://fixture").searchParams;
    if (params.has("watch")) {
      versions.push(params.get("resourceVersion")!); streams.push(res);
      if (allowWatch) { res.writeHead(200); res.flushHeaders(); }
    } else if (params.get("limit") === "100") {
      pages++;
      const metadata = { resourceVersion: "10", ...(params.has("continue") ? {} : { continue: "next", remainingItemCount: 1 }) };
      res.end(JSON.stringify({ kind: "PodList", apiVersion: "v1", metadata, items: [manifest(params.has("continue") ? "b" : "a", "9")] }));
    } else { direct++; res.end(JSON.stringify({ items: [manifest("remote", "8")] })); }
  };
  try {
    const cold = await f.client.list(q);
    expect(cold.items[0]?.metadata?.name).toBe("remote");
    await until(() => streams.length === 1);
    expect(await f.client.list(q)).toEqual(cold);
    allowWatch = true; streams[0]!.writeHead(200); streams[0]!.flushHeaders();
    const view = f.client.inNamespace("first");
    await until(async () => (await view.list(q)).kind === "PodList");
    const count = direct;
    const initial = await f.client.clusterResources().list({ ...q, namespace: "first" });
    expect(initial).toEqual({ apiVersion: "v1", kind: "PodList", metadata: { resourceVersion: "10" }, items: [manifest("a", "9"), manifest("b", "9")] });
    initial.items[0]!.metadata!.name = "mutated";
    expect((await f.client.list(q)).items[0]?.metadata?.name).toBe("a");
    await expect(f.client.list(q, { signal: AbortSignal.abort(new Error("cancelled")) })).rejects.toThrow("cancelled");
    await expect(f.client.list(q, { timeoutMs: 0 })).rejects.toMatchObject({ kind: "invalid_argument" });
    const events = [["ADDED", "c", "11"], ["MODIFIED", "a", "12"], ["DELETED", "b", "13"]]
      .map(([type, name, rv]) => JSON.stringify({ type, object: manifest(name, rv) }));
    events.push(JSON.stringify({ type: "BOOKMARK", object: { metadata: { resourceVersion: "14" } } }));
    const data = events.join("\n") + "\n";
    streams[0]!.write(data.slice(0, 10)); streams[0]!.write(data.slice(10));
    await until(async () => (await f.client.list(q)).metadata?.resourceVersion === "14");
    expect((await f.client.list(q)).items).toEqual([manifest("a", "12"), manifest("c", "11")]);
    expect(direct).toBe(count); expect(pages).toBe(2);
    expect(await f.client.list({ ...q, limit: 1, continue: "server-token" })).toEqual(cold);
    allowWatch = false; streams[0]!.end();
    await until(async () => (await f.client.list(q)).items[0]?.metadata?.name === "remote");
    await until(() => streams.length === 2);
    streams[1]!.writeHead(200); streams[1]!.flushHeaders();
    await until(async () => (await f.client.list(q)).metadata?.resourceVersion === "14");
    expect(versions).toEqual(["10", "14"]); expect(pages).toBe(2);
    for (const { path, authorization } of f.requests) {
      const params = new URL(path, "http://fixture").searchParams;
      expect(params.get("labelSelector")).toBe(q.labelSelector);
      expect(params.get("fieldSelector")).toBe(q.fieldSelector);
      expect(authorization).toBe("Bearer fixture-token");
    }
  } finally { await f.dispose(); }
});

for (const expiry of ["http", "event"]) {
  test(`list falls back through ${expiry} 410 and returns an empty cached collection after relist`, async () => {
    const f = await fixture(false, true);
    let lists = 0;
    let watch: ServerResponse | undefined;
    let replacementWatch: ServerResponse | undefined;
    let direct = 0;
    let failDirect = false;
    f.respond = (req, res) => {
      const params = new URL(req.url!, "http://fixture").searchParams;
      if (params.has("limit")) {
        lists++;
        res.end(JSON.stringify({ metadata: { resourceVersion: String(lists) }, items: lists === 1 ? [manifest("old", "1")] : [] }));
      } else if (params.has("watch")) {
        if (params.get("resourceVersion") === "1") {
          if (expiry === "http") { res.writeHead(410); res.end(); }
          else { watch = res; res.writeHead(200); res.flushHeaders(); }
        } else replacementWatch = res;
      } else {
        direct++;
        if (failDirect) { res.writeHead(403); res.end(); }
        else res.end(JSON.stringify({ items: [manifest("direct", "1")] }));
      }
    };
    try {
      await f.client.list(pods);
      if (expiry === "event") {
        await until(async () => (await f.client.list(pods)).items[0]?.metadata?.name === "old");
        watch!.write(JSON.stringify({ type: "ERROR", object: { code: 410 } }) + "\n");
      }
      await until(() => !!replacementWatch);
      expect((await f.client.list(pods)).items[0]?.metadata?.name).toBe("direct");
      failDirect = true;
      await expect(f.client.list(pods)).rejects.toMatchObject({ kind: "permission_denied", code: 403 });
      failDirect = false;
      replacementWatch!.writeHead(200); replacementWatch!.flushHeaders();
      await until(async () => (await f.client.list(pods)).items.length === 0);
      const before = direct;
      expect((await f.client.list(pods)).items).toEqual([]);
      expect(direct).toBe(before); expect(lists).toBe(2);
    } finally { await f.dispose(); }
  });
}

test("a failed watch reconnects from the last good event; closing the client cancels pending sync", async () => {
  const f = await fixture(false, true);
  const versions: string[] = [];
  let watch: ServerResponse | undefined;
  let listing = false;
  f.respond = (req, res) => {
    const params = new URL(req.url!, "http://fixture").searchParams;
    if (params.has("watch")) {
      versions.push(params.get("resourceVersion")!);
      watch = res; res.writeHead(200); res.flushHeaders();
    } else if (params.has("limit") && req.url!.includes("/services")) listing = true;
    else res.end(JSON.stringify({ metadata: { resourceVersion: "10" }, items: [] }));
  };
  try {
    await f.client.list(pods);
    await until(() => !!watch);
    watch!.write("x".repeat(700)); watch!.write("x".repeat(700));
    await until(() => versions.length === 2);
    expect(versions).toEqual(["10", "10"]);
    await f.client.list({ apiVersion: "v1", resource: "services" });
    await until(() => listing);
    await f.client.dispose();
    expect(() => f.client.list(pods)).toThrow("disposed");
  } finally { await f.dispose(); }
});
