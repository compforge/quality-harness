import { expect, test, spyOn } from "bun:test";
import { Headers } from "node-fetch";
import { createServer, type IncomingMessage, type ServerResponse } from "node:http";
import { createServer as createHttpsServer } from "node:https";
import { mkdtempSync, readFileSync, rmSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { KubernetesClient } from "../src/kubernetes/client";
import { KubectlExecutor, type ExecResult } from "../src/kubernetes/executor";
import { kubernetesCommandError } from "../src/errors";
import { resourceGetCommand } from "../src/kubernetes/resource-command";
import { PooledKubeConfig } from "../src/kubernetes/pooled-config";

const result = (): ExecResult => ({ ok: true, stdout: "compatibility", stderr: "", exitCode: 0, timedOut: false, durationMs: 0, command: ["kubectl"] });
const tlsFixture = (name: string) => readFileSync(join(import.meta.dir, "fixtures/pod-log-tls", name));

async function fixture(tls = false) {
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
  const kube = { namespace: "first", kubeconfig };
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

for (const tls of [false, true]) {
  test(`JSON GET/LIST share one ${tls ? "mutually authenticated TLS" : "TCP"} connection across namespaces`, async () => {
    const f = await fixture(tls);
    try {
      const streamed: Uint8Array[] = [];
      expect((await f.client.run("first", ["get", "services", "api", "-o", "json"])).ok).toBe(true);
      const next = await f.client.run("second", ["get", "pods", "-l", "app=api", "-o", "json"], {
        collectStdout: false, onStdoutBytes: bytes => streamed.push(bytes),
      });
      expect(next.ok).toBe(true); expect(next.stdout).toBe("");
      expect(next.command[0]).toBe("kubernetes-api");
      expect(JSON.parse(new TextDecoder().decode(streamed[0]))).toEqual({ items: [] });
      expect(f.connections).toBe(1); expect(f.fallbacks).toBe(0);
      expect(f.requests).toEqual([
        { path: "/api/v1/namespaces/first/services/api", authorization: "Bearer fixture-token" },
        { path: "/api/v1/namespaces/second/pods?labelSelector=app%3Dapi", authorization: "Bearer fixture-token" },
      ]);
      expect((await f.client.run("first", ["get", "deployments.apps", "api", "-o", "json"])).ok).toBe(true);
      expect(f.requests[2]?.path).toBe("/apis/apps/v1/namespaces/first/deployments/api");
      expect(f.connections).toBe(1);
    } finally { await f.dispose(); }
  });
}

test("native errors never replay, preserve HTTP failure meaning, and omit response bodies", async () => {
  const f = await fixture();
  try {
    for (const [status, kind] of [[401, "authentication_failed"], [403, "permission_denied"],
      [404, "resource_not_found"], [429, "limit_exceeded"], [500, "operation_failed"]] as const) {
      f.respond = (_request, response) => { response.writeHead(status); response.end("private-response-body"); };
      const failed = await f.client.run("first", ["get", "secrets", "credentials", "-o", "json"]);
      expect(failed.ok).toBe(false);
      expect(failed.stderr).not.toContain("private-response-body");
      expect(failed.command[0]).toBe("kubernetes-api");
      expect(kubernetesCommandError(failed).kind).toBe(kind);
    }
    expect(f.requests).toHaveLength(5); expect(f.fallbacks).toBe(0);
  } finally { await f.dispose(); }
});

test("per-call deadlines, byte limits and abort preserve the pool for later reads", async () => {
  const f = await fixture();
  try {
    f.respond = () => {};
    const timedOut = await f.client.run("first", ["get", "pods", "-o", "json"], { timeoutMs: 30 });
    expect(timedOut.ok).toBe(false); expect(timedOut.timedOut).toBe(true);
    const controller = new AbortController();
    const aborted = f.client.run("first", ["get", "pods", "-o", "json"], { signal: controller.signal });
    setTimeout(() => controller.abort(new Error("cancel request")), 15);
    expect((await aborted).stderr).toBe("[aborted]");
    f.respond = (_request, response) => response.end("x".repeat(2048));
    expect((await f.client.run("first", ["get", "pods", "-o", "json"])).ok).toBe(false);
    f.respond = (_request, response) => response.end(JSON.stringify({ items: [] }));
    expect((await f.client.run("first", ["get", "pods", "-o", "json"])).ok).toBe(true);
    f.respond = () => {};
    const active = f.client.run("first", ["get", "pods", "-o", "json"]);
    await new Promise(resolve => setTimeout(resolve, 15));
    await f.client.dispose();
    expect((await active).ok).toBe(false);
    expect(() => f.client.run("first", ["get", "pods", "-o", "json"])).toThrow("disposed");
    expect(f.fallbacks).toBe(0);
  } finally { await f.dispose(); }
});

test("uncovered argv, stdin, exec and custom executors keep their existing behavior", async () => {
  const f = await fixture();
  try {
    for (const command of [["get", "pods"], ["get", "pods", "-o", "yaml"],
      ["get", "pods", "--watch", "-o", "json"], ["get", "custom.example", "-o", "json"]]) {
      expect((await f.client.run("first", command)).stdout).toBe("compatibility");
    }
    expect((await f.client.run("first", ["get", "pods", "-o", "json"], { stdin: "input" })).stdout).toBe("compatibility");
    expect((await f.client.exec("first", { pod: "pod", container: "main" }, ["printenv", "-0"])).stdout).toBe("compatibility");
    expect(f.fallbacks).toBe(6); expect(f.requests).toHaveLength(0);
    const custom = new KubernetesClient({ namespace: "first", kubeconfig: "/missing" }, undefined, {
      run: async () => result(), exec: async () => result(),
    });
    try { expect((await custom.run("first", ["get", "pods", "-o", "json"])).stdout).toBe("compatibility"); }
    finally { await custom.dispose(); }
  } finally { await f.dispose(); }
});

test("request matching rejects kubectl syntax with different semantics", () => {
  expect(resourceGetCommand(["get", "pods.apps", "-o", "json"])).toBeUndefined();
  expect(resourceGetCommand(["get", "constructor", "-o", "json"])).toBeUndefined();
  expect(resourceGetCommand(["get", "pods", "pod-a", "pod-b", "-o", "json"])).toBeUndefined();
  expect(resourceGetCommand(["get", "pods", "pod-a,pod-b", "-o", "json"])).toBeUndefined();
  expect(resourceGetCommand(["get", "pods", "-l", "--watch", "-o", "json"])).toBeUndefined();
  expect(resourceGetCommand(["get", "pods", "--all-namespaces", "-o", "json"])).toBeUndefined();
});

test("credential headers refresh while TLS identity changes select a different Agent", async () => {
  const config = new PooledKubeConfig(1);
  config.loadFromOptions({ clusters: [{ name: "fixture", server: "https://localhost" }],
    users: [{ name: "fixture", token: "first-token" }],
    contexts: [{ name: "fixture", cluster: "fixture", user: "fixture" }], currentContext: "fixture" });
  try {
    const first = await config.applyToFetchOptions({});
    config.users = config.users.map(user => ({ ...user, token: "second-token" }));
    const second = await config.applyToFetchOptions({});
    expect(second.agent).toBe(first.agent);
    expect(new Headers(second.headers).get("authorization")).toBe("Bearer second-token");
    config.clusters = config.clusters.map(cluster => ({ ...cluster, skipTLSVerify: true }));
    const changed = await config.applyToFetchOptions({});
    expect(changed.agent).not.toBe(first.agent);
  } finally { config.dispose(); }
});


test("native reads preserve TLS verification failures and never fall back", async () => {
  const f = await fixture(true);
  try {
    const document = JSON.parse(readFileSync(f.kubeconfig, "utf8"));
    delete document.clusters[0].cluster["certificate-authority-data"];
    writeFileSync(f.kubeconfig, JSON.stringify(document));
    const result = await f.client.run("first", ["get", "pods", "-o", "json"]);
    expect(result.ok).toBe(false);
    expect(result.command[0]).toBe("kubernetes-api");
    expect(f.fallbacks).toBe(0); expect(f.requests).toHaveLength(0);
  } finally { await f.dispose(); }
});


test("environment proxy routing retains the kubectl path", async () => {
  const f = await fixture();
  const previous = process.env.HTTPS_PROXY;
  let client: KubernetesClient;
  try {
    process.env.HTTPS_PROXY = "http://proxy.invalid:1234";
    client = new KubernetesClient({ namespace: "first", kubeconfig: f.kubeconfig }, undefined, f.executor);
  } finally {
    if (previous === undefined) delete process.env.HTTPS_PROXY;
    else process.env.HTTPS_PROXY = previous;
  }
  try {
    expect((await client.run("first", ["get", "pods", "-o", "json"])).stdout).toBe("compatibility");
    expect(f.fallbacks).toBe(1); expect(f.requests).toHaveLength(0);
  } finally { await client.dispose(); await f.dispose(); }
});


test("credential preparation observes the request deadline before dispatch", async () => {
  const f = await fixture();
  let complete!: (value: Awaited<ReturnType<PooledKubeConfig["applyToFetchOptions"]>>) => void;
  const prepared = new Promise<Awaited<ReturnType<PooledKubeConfig["applyToFetchOptions"]>>>(resolve => { complete = resolve; });
  const auth = spyOn(PooledKubeConfig.prototype, "applyToFetchOptions").mockImplementation(() => prepared);
  try {
    const timedOut = await f.client.run("first", ["get", "pods", "-o", "json"], { timeoutMs: 20 });
    expect(timedOut.timedOut).toBe(true);
    await f.client.dispose();
    complete({ headers: {} });
    await prepared;
    expect(f.requests).toHaveLength(0); expect(f.fallbacks).toBe(0);
  } finally { auth.mockRestore(); complete({ headers: {} }); await f.dispose(); }
});
