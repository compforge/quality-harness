import { expect, test } from "bun:test";
import { SocketClosedUnexpectedlyError } from "@redis/client";
import { RedisAccess, RedisClient, RedisConnection, discoverRedisTopology,
  type RedisManagedConnection } from "../src/redis";
import type { TcpTransport } from "../src/transport";

const endpoint = { host: "redis.internal", port: 6379 };
const transport = (name: string, connect: TcpTransport["connect"]): TcpTransport => ({ kind: "tcp", name, connect });
function connection(host = endpoint.host): RedisManagedConnection {
  let closed = false;
  return { endpoint: { host, port: 6379 }, get isReady() { return !closed; }, close() { closed = true; },
    ping: async () => "PONG", info: async () => ({}), dbSize: async () => 0, command: async () => null,
    pipeline: async () => [], scan: async () => ({ cursor: "0", keys: [] }) };
}
function gate() {
  let release!: () => void;
  const promise = new Promise<void>(resolve => { release = resolve; });
  return { promise, release };
}

test("Redis uses the first successful handshake and shares concurrent acquisition", async () => {
  let forwards = 0;
  let handshakes = 0;
  const access = new RedisAccess([
    transport("native", async target => target),
    transport("relay", async () => { forwards++; return { host: "localhost", port: 1 }; }),
  ], { timeoutMs: 100, useSsl: false }, async () => { handshakes++; return connection(); });
  try {
    const [first, second] = await Promise.all([access.connection(endpoint, 0), access.connection(endpoint, 0)]);
    expect(first).toBe(second);
    expect(handshakes).toBe(1);
    expect(forwards).toBe(0);
  } finally { await access.close(); }
});

test("Redis handshake network failures advance to relay and report the selected route", async () => {
  for (const failure of [Object.assign(new Error("refused"), { code: "ECONNREFUSED" }), new SocketClosedUnexpectedlyError()]) {
    const routes: unknown[] = [];
    const hosts: string[] = [];
    const access = new RedisAccess([
      transport("native", async target => target),
      transport("pod-relay", async () => ({ host: "localhost", port: 16379 })),
    ], { timeoutMs: 100, useSsl: false }, async (_endpoint, mapped) => {
      hosts.push(mapped.host);
      if (mapped.host === endpoint.host) throw failure;
      return connection();
    }, { onRoute: route => routes.push(route) });
    try {
      expect(await (await access.connection(endpoint, 0)).ping()).toBe("PONG");
      expect(hosts).toEqual([endpoint.host, "localhost"]);
      expect(routes).toEqual([{ transport: "pod-relay", reason: failure instanceof SocketClosedUnexpectedlyError ? "connection_lost" : "ECONNREFUSED" }]);
    } finally { await access.close(); }
  }
});

test("Redis authentication and TLS verification failures never change route", async () => {
  for (const error of [new Error("WRONGPASS"), Object.assign(new Error("certificate"), { code: "CERT_HAS_EXPIRED" })]) {
    let relay = 0;
    const access = new RedisAccess([transport("native", async target => target),
      transport("relay", async target => { relay++; return target; })],
    { timeoutMs: 100, useSsl: false }, async () => { throw error; });
    try {
      await expect(access.connection(endpoint, 0)).rejects.toBe(error);
      expect(relay).toBe(0);
    } finally { await access.close(); }
  }
});

test("Redis mapping and handshake candidates share one deadline", async () => {
  let factories = 0;
  let budgetAtRelay = 0;
  const access = new RedisAccess([
    transport("native", async target => { await new Promise(resolve => setTimeout(resolve, 35)); return target; }),
    transport("relay", async () => ({ host: "localhost", port: 16379 })),
  ], { timeoutMs: 1000, acquireTimeoutMs: 80, useSsl: false }, async (_endpoint, mapped, config) => {
    factories++;
    if (mapped.host === endpoint.host) throw Object.assign(new Error("reset"), { code: "ECONNRESET" });
    budgetAtRelay = config.connectTimeoutMs!;
    return new Promise(() => {});
  });
  try {
    await expect(access.connection(endpoint, 0)).rejects.toThrow("connection timed out after 80ms");
    expect(factories).toBe(2);
    expect(budgetAtRelay).toBeGreaterThan(0);
    expect(budgetAtRelay).toBeLessThan(60);
  } finally { await access.close(); }
});

test("Redis cancellation prevents late mapping from starting a handshake", async () => {
  const mapped = gate();
  const started = gate();
  const controller = new AbortController();
  let handshakes = 0;
  const access = new RedisAccess(transport("relay", async target => { started.release(); await mapped.promise; return target; }),
    { timeoutMs: 1000, useSsl: false }, async () => { handshakes++; return connection(); }, { signal: controller.signal });
  const pending = access.connection(endpoint, 0).catch(error => error);
  await started.promise;
  controller.abort(new Error("cancelled by caller"));
  expect((await pending).message).toBe("cancelled by caller");
  mapped.release();
  await access.close();
  expect(handshakes).toBe(0);
});

test("Redis cancellation destroys a pending native handshake", async () => {
  const controller = new AbortController();
  const started = gate();
  let open = false;
  let closes = 0;
  const client: any = { on() { return this; }, get isOpen() { return open; },
    connect() { open = true; started.release(); return new Promise(() => {}); },
    destroy() { open = false; closes++; } };
  const pending = RedisConnection.connect(endpoint, endpoint,
    { timeoutMs: 1000, useSsl: false, database: 0, signal: controller.signal }, () => client).catch(error => error);
  await started.promise;
  controller.abort(new Error("stop"));
  expect((await pending).message).toBe("stop");
  expect(closes).toBe(1);
});

test("Redis never replays a command or a pipeline after losing the response", async () => {
  for (const pipeline of [false, true]) {
    let calls = 0;
    let ready = true;
    const failure = Object.assign(new Error("response lost"), { code: "ECONNRESET" });
    const send = async () => { calls++; ready = false; throw failure; };
    const client: any = { isOpen: true, get isReady() { return ready; }, on() { return this; }, connect: async () => {},
      sendCommand: send, multi() { return { addCommand() {}, execAsPipeline: send }; }, destroy() { this.isOpen = false; } };
    const socket = await RedisConnection.connect(endpoint, endpoint, { timeoutMs: 100, useSsl: false, database: 0 }, () => client);
    try {
      await expect(pipeline ? socket.pipeline([["INCR", "counter"]]) : socket.command(["INCR", "counter"])).rejects.toBe(failure);
      expect(calls).toBe(1);
    } finally { socket.close(); }
  }
});

test("retained Redis connections stop active commands when the root is cancelled", async () => {
  const controller = new AbortController();
  const started = gate();
  const client: any = { isOpen: true, isReady: true, on() { return this; }, connect: async () => {},
    sendCommand() { started.release(); return new Promise(() => {}); }, destroy() { this.isOpen = false; } };
  const socket = await RedisConnection.connect(endpoint, endpoint,
    { timeoutMs: 1000, useSsl: false, database: 0, signal: controller.signal }, () => client);
  const pending = socket.command(["GET", "key"]).catch(error => error);
  await started.promise;
  controller.abort(new Error("command disposed"));
  expect(await pending).toEqual(new Error("command disposed"));
  expect(client.isOpen).toBeFalse();
  await expect(socket.ping()).rejects.toThrow("command disposed");
});

test("Sentinel discovery and discovered masters share transports with separate credentials", async () => {
  const seen: Array<{ host: string; username?: string; database: number }> = [];
  const access = new RedisAccess([
    transport("native", async () => { throw Object.assign(new Error("unreachable"), { code: "EHOSTUNREACH" }); }),
    transport("relay", async () => ({ host: "localhost", port: 16379 })),
  ], { timeoutMs: 100, useSsl: false, username: "data-reader", password: "data-secret" }, async (target, _mapped, config) => {
    seen.push({ host: target.host, username: config.username, database: config.database });
    return { ...connection(target.host), command: async args => args[1] === "GET-MASTER-ADDR-BY-NAME" ? ["redis-master", "6379"] : [] };
  });
  try {
    const topology = await discoverRedisTopology(access, { clusterType: "sentinel", endpoints: [{ host: "sentinel", port: 26379 }],
      database: 7, sentinelMasterName: "primary", sentinelUsername: "sentinel-reader", sentinelPassword: "sentinel-secret" });
    await access.connection(topology.masters[0]!, 7);
    expect(seen).toEqual([{ host: "sentinel", username: "sentinel-reader", database: 0 },
      { host: "redis-master", username: "data-reader", database: 7 }]);
  } finally { await access.close(); }
});

test("RedisClient retains topology configuration and rejects a nonzero Cluster DB", async () => {
  const client = new RedisClient({ resolve: async () => ({ clusterType: "cluster", endpoints: [endpoint], database: 7, useSsl: false, timeoutMs: 100 }),
    transports: [transport("native", async target => target)] });
  await expect(client.initialize()).rejects.toThrow("Cluster requires database 0");
  await client.dispose();
});


test("Redis route timeout leaves budget for relay and cannot start a late handshake", async () => {
  const routes: unknown[] = [];
  const pendingMapping = gate();
  const hosts: string[] = [];
  const access = new RedisAccess([
    transport("native", async target => { await pendingMapping.promise; return target; }),
    transport("relay", async () => ({ host: "localhost", port: 16379 })),
  ], { timeoutMs: 100, connectTimeoutMs: 20, acquireTimeoutMs: 150, useSsl: false }, async (_target, mapped) => {
    hosts.push(mapped.host);
    return connection();
  }, { onRoute: route => routes.push(route) });
  try {
    expect(await (await access.connection(endpoint, 0)).ping()).toBe("PONG");
    pendingMapping.release();
    await Promise.resolve();
    expect(hosts).toEqual(["localhost"]);
    expect(routes).toEqual([{ transport: "relay", reason: "ETIMEDOUT" }]);
  } finally { pendingMapping.release(); await access.close(); }
});
