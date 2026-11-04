import { expect, test } from "bun:test";
import { createServer } from "node:http";
import { ClientManager } from "../src/client-manager";
import { ServicePortForwardTransport, Transport } from "../src/transport";
import { OpenSearchClient, openSearchServername } from "../src/opensearch";
import { S3Client } from "../src/s3";
import type { Executor } from "../src/kubernetes/executor";
import type { StartPortForward, PortForward } from "../src/kubernetes/port-forward";

const serviceExecutor: Executor = {
  run: async command => ({ ok: true, exitCode: 0, command, durationMs: 0, timedOut: false, stderr: "",
    stdout: JSON.stringify({ items: command.includes("services") ? [{ metadata: { name: "store", namespace: "dev" },
      spec: { clusterIP: "10.0.0.1", ports: [{ port: 9200 }] } }] : [] }) }),
  exec: async () => { throw new Error("unexpected exec"); },
};

test("manager disposal cancels discovery that has not published a transport yet", async () => {
  const root = new ClientManager();
  let started!: () => void;
  const discovering = new Promise<void>(resolve => { started = resolve; });
  let aborted = false;
  const executor: Executor = { ...serviceExecutor, run: async (_command, options) => {
    started();
    await new Promise<void>((_resolve, reject) => {
      options!.signal!.addEventListener("abort", () => { aborted = true; reject(options!.signal!.reason); }, { once: true });
    });
    throw new Error("unreachable");
  } };
  const acquired = root.get({ clientKey: "forward", createClient: (_deps, signal) =>
    new ServicePortForwardTransport({ namespace: "dev", signal }, { executor }) });
  await discovering;
  const closed = root.dispose();
  await expect(acquired).rejects.toThrow("disposed");
  await closed;
  expect(aborted).toBe(true);
});

function forwardHandle(port: number): PortForward {
  let alive = true;
  let finish!: (code: number) => void;
  const exited = new Promise<number>(resolve => { finish = resolve; });
  return { target: { kind: "service", name: "store" }, command: [], localPort: port, exited,
    isAlive: () => alive, stop: () => { alive = false; finish(0); } };
}

test("cancelling one borrower keeps shared forward startup alive for another client", async () => {
  let release!: () => void;
  const gate = new Promise<void>(resolve => { release = resolve; });
  let starts = 0;
  const forward = forwardHandle(19200);
  const transport = new ServicePortForwardTransport({ namespace: "dev" }, { executor: serviceExecutor,
    startForward: async () => { starts++; await gate; return { ok: true, value: forward }; } });
  await transport.initialize();
  const cancelled = new AbortController();
  const reason = new Error("first caller finished");
  const first = transport.connect({ host: "store", port: 9200 }, { signal: cancelled.signal, remainingMs: () => 1000 });
  const second = transport.connect({ host: "store", port: 9200 });
  cancelled.abort(reason);
  await expect(first).rejects.toBe(reason);
  release();
  expect(await second).toEqual({ host: "127.0.0.1", port: 19200 });
  expect(starts).toBe(1);
  expect(forward.isAlive!()).toBe(true);
  await transport.dispose();
  expect(forward.isAlive!()).toBe(false);
});

test("an exited forward is replaced on the next acquisition", async () => {
  const forwards: PortForward[] = [];
  const transport = new ServicePortForwardTransport({ namespace: "dev" }, { executor: serviceExecutor,
    startForward: async () => {
      const forward = forwardHandle(19200 + forwards.length);
      forwards.push(forward);
      return { ok: true, value: forward };
    } });
  expect((await transport.connect({ host: "store", port: 9200 })).port).toBe(19200);
  forwards[0]!.stop();
  expect((await transport.connect({ host: "store", port: 9200 })).port).toBe(19201);
  expect(forwards).toHaveLength(2);
  await transport.dispose();
  expect(forwards.every(forward => !forward.isAlive!())).toBe(true);
});

test("root disposal waits for and stops a forward returned after cancellation", async () => {
  let release!: () => void;
  let started!: () => void;
  const gate = new Promise<void>(resolve => { release = resolve; });
  const startup = new Promise<void>(resolve => { started = resolve; });
  const forward = forwardHandle(19200);
  const startForward: StartPortForward = async () => { started(); await gate; return { ok: true, value: forward }; };
  const transport = new ServicePortForwardTransport({ namespace: "dev" }, { executor: serviceExecutor, startForward });
  const connected = transport.connect({ host: "store", port: 9200 });
  await startup;
  let disposed = false;
  const closing = transport.dispose().then(() => { disposed = true; });
  await expect(connected).rejects.toThrow("disposed");
  expect(disposed).toBe(false);
  release();
  await closing;
  expect(forward.isAlive!()).toBe(false);
});

test("root cancellation stops acquisition but keeps the forward alive until clients are closed", async () => {
  const root = new ClientManager();
  const events: string[] = [];
  const forward = forwardHandle(19200);
  const transport = await root.get({ clientKey: "forward", createClient: (_deps, signal) =>
    new ServicePortForwardTransport({ namespace: "dev", signal }, { executor: serviceExecutor,
      startForward: async options => {
        options.signal!.addEventListener("abort", () => { events.push("transport"); forward.stop(); });
        return { ok: true, value: forward };
      } }) });
  await root.create({ createClient: () => ({
    initialize: async () => { await transport.connect({ host: "store", port: 9200 }); },
    dispose: async () => { expect(forward.isAlive!()).toBe(true); events.push("client"); },
  }) });
  await root.dispose();
  expect(events).toEqual(["client", "transport"]);
});

test("short-lived protocol clients reuse one lazy forward until the root scope ends", async () => {
  const server = createServer((request, response) => {
    expect(request.headers.host).toBe("store.dev.svc:9200");
    response.setHeader("content-type", "application/json");
    response.end(request.method === "HEAD" ? undefined : '{"count":7}');
  });
  await new Promise<void>(resolve => server.listen(0, "127.0.0.1", resolve));
  const port = (server.address() as { port: number }).port;
  let starts = 0, stops = 0;
  const executor: Executor = {
    run: async command => ({ ok: true, exitCode: 0, command, durationMs: 0, timedOut: false, stderr: "",
      stdout: JSON.stringify({ items: command.includes("services") ? [{ metadata: { name: "store", namespace: "dev" },
        spec: { clusterIP: "10.0.0.1", ports: [{ port: 9200 }] } }] : [] }) }),
    exec: async () => { throw new Error("unexpected exec"); },
  };
  const root = new ClientManager();
  const source = { clientKey: "forward:dev", createClient: (_deps: Pick<ClientManager, "get">, signal: AbortSignal) =>
    new ServicePortForwardTransport({ namespace: "dev", signal }, { executor, startForward: async options => {
      starts++;
      let alive = true;
      let exited!: (code: number) => void;
      const completion = new Promise<number>(resolve => { exited = resolve; });
      return { ok: true, value: { target: options.target!, command: [], localPort: port, exited: completion,
        isAlive: () => alive, stop: () => { if (alive) { alive = false; stops++; exited(0); } } } };
    } }) };
  try {
    expect(starts).toBe(0);
    const transport = await root.get(source);
    expect(starts).toBe(0);
    const search = await root.create({ createClient: (_deps, signal) => new OpenSearchClient({
      node: "http://store.dev.svc:9200", auth: {},
    }, transport, { signal }) });
    expect(await search.count("spans", {})).toBe(7);
    await search.dispose();
    expect(stops).toBe(0);
    const objects = await root.create({ createClient: (_deps, signal) => new S3Client({
      endpoint: "http://store.dev.svc:9200", region: "test", credentials: { accessKeyId: "key", secretAccessKey: "secret" },
    }, transport, { concurrency: 1, connectTimeoutMs: 1000, requestTimeoutMs: 1000 }, { signal }) });
    await objects.headBucket("bucket");
    await objects.dispose();
    expect(starts).toBe(1);
    expect(stops).toBe(0);
    await root.dispose();
    expect(stops).toBe(1);
    await expect(transport.connect({ host: "store", port: 9200 })).rejects.toThrow("disposed");
  } finally { await root.dispose(); server.closeAllConnections(); await new Promise<void>(resolve => server.close(() => resolve())); }
});

test("transport retains late rollback failures for root disposal", async () => {
  let release!: () => void;
  const ready = new Promise<void>(resolve => { release = resolve; });
  const transport = new Transport([{ kind: "tcp", name: "delayed", connect: async endpoint => endpoint }]);
  const cleanup = new Error("late close failed");
  const pending = transport.acquire({ name: "fixture", connectTimeoutMs: 10,
    open: async () => { await ready; return { connection: {}, transport: "delayed" }; },
    close: async () => { throw cleanup; }, retryReason: () => undefined });
  await expect(pending).rejects.toMatchObject({ kind: "timeout" });
  release();
  await expect(transport.dispose()).rejects.toMatchObject({ errors: [cleanup] });
});

test("TLS identity excludes IPv4 and bracketed IPv6 literals", () => {
  expect(openSearchServername("127.0.0.1")).toBeUndefined();
  expect(openSearchServername("[::1]")).toBeUndefined();
  expect(openSearchServername("::1")).toBeUndefined();
  expect(openSearchServername("search.example")).toBe("search.example");
});
