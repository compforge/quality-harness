import { expect, test } from "bun:test";
import { setImmediate } from "node:timers/promises";
import { spawnSync } from "node:child_process";
import { fileURLToPath } from "node:url";
import type { Connection } from "mysql2/promise";
import { MysqlClient, MysqlDatabase } from "../src/mysql";
import { OpenSearchClient } from "../src/opensearch";
import { RedisClient, RedisAccess, type RedisManagedConnection } from "../src/redis";
import type { Client } from "../src/client";
import type { TcpTransport } from "../src/transport";
import { TimeoutError, ToolboxError } from "../src/errors";

function gate() {
  let release!: () => void;
  const promise = new Promise<void>(resolve => { release = resolve; });
  return { promise, release };
}

async function bounded<T>(pending: Promise<T>): Promise<T> {
  let timer: ReturnType<typeof setTimeout> | undefined;
  try {
    return await Promise.race([pending, new Promise<never>((_, reject) => {
      timer = setTimeout(() => reject(new Error("Cancellation did not finish while preparation was blocked")), 500);
    })]);
  } finally { clearTimeout(timer); }
}

const endpoint = { host: "example.invalid", port: 12345 };
const mysqlTarget = { ...endpoint, database: "db", user: "reader", password: "" };

test.each(["mysql", "redis"] as const)("%s exposes a typed acquisition deadline", async protocol => {
  const transport: TcpTransport = { kind: "tcp", name: "blocked", connect: () => new Promise(() => {}) };
  let pending: Promise<unknown>, close: () => Promise<void>;
  if (protocol === "mysql") {
    const db = new MysqlDatabase([transport], { connectTimeoutMs: 20, acquireTimeoutMs: 1000, queryTimeoutMs: 100 });
    pending = db.initialize(mysqlTarget); close = () => db.close();
  } else {
    const db = new RedisAccess(transport, { timeoutMs: 100, connectTimeoutMs: 20, acquireTimeoutMs: 1000, useSsl: false });
    pending = db.connection(endpoint, 0); close = () => db.close();
  }
  try {
    const error = await pending.catch(error => error);
    expect(error).toBeInstanceOf(TimeoutError);
    expect(error).toBeInstanceOf(ToolboxError);
    expect(error).toMatchObject({ kind: "timeout", code: "ETIMEDOUT" });
  } finally { await close(); }
});

for (const protocol of ["mysql", "opensearch", "redis"] as const) {
  for (const phase of ["resolve", "mapping"] as const) {
    test(`${protocol} cancels ${phase}, disposes promptly, and does not resume the late route`, async () => {
      const started = gate(), blocked = gate();
      const controller = new AbortController();
      const reason = new Error("caller stopped acquisition");
      let mappings = 0, fallback = 0, published = 0;
      const pause = async (current: typeof phase) => {
        if (phase === current) { started.release(); await blocked.promise; }
      };
      const transports: TcpTransport[] = [
        { kind: "tcp", name: "blocked", connect: async () => { mappings++; await pause("mapping"); return endpoint; } },
        { kind: "tcp", name: "unexpected", connect: async () => { fallback++; return endpoint; } },
      ];
      const lifecycle = { signal: controller.signal, onRoute: () => { published++; } };
      let client: Client, run: () => Promise<unknown>;
      if (protocol === "mysql") {
        client = new MysqlClient({ transports, resolve: async () => { await pause("resolve"); return mysqlTarget; } },
          { ...lifecycle, connectTimeoutMs: 30_000, queryTimeoutMs: 30_000 });
        run = () => client.initialize();
      } else if (protocol === "opensearch") {
        client = new OpenSearchClient({ transports, resolve: async () => {
          await pause("resolve"); return { node: "http://example.invalid:9200", auth: {}, connectTimeoutMs: 30_000 };
        } }, lifecycle);
        run = () => client.initialize();
      } else {
        const redis = new RedisClient({ transports, resolve: async () => {
          await pause("resolve"); return { clusterType: "single" as const, endpoints: [endpoint], database: 0, useSsl: false, timeoutMs: 30_000 };
        } }, lifecycle);
        client = redis;
        run = async () => { await redis.initialize(); return redis.access.connection(endpoint, 0); };
      }
      const pending = run().catch(error => error);
      try {
        await started.promise;
        controller.abort(reason);
        expect(await bounded(pending)).toBe(reason);
        await bounded(client.dispose());
        blocked.release();
        await setImmediate();
        expect(mappings).toBe(phase === "mapping" ? 1 : 0);
        expect(fallback).toBe(0);
        expect(published).toBe(0);
      } finally { blocked.release(); await client.dispose(); }
    });
  }
}

test("MySQL mapping timeout advances routes without starting a late handshake", async () => {
  const blocked = gate();
  let handshakes = 0, closed = 0;
  const routes: unknown[] = [];
  const connection = { destroy() {}, connection: { stream: { destroy() { closed++; } } } } as unknown as Connection;
  const db = new MysqlDatabase([
    { kind: "tcp", name: "slow", connect: async () => { await blocked.promise; return endpoint; } },
    { kind: "tcp", name: "ready", connect: async () => endpoint },
  ], { connectTimeoutMs: 40, acquireTimeoutMs: 1000, queryTimeoutMs: 100, onRoute: route => routes.push(route) },
  async () => { handshakes++; return connection; });
  try {
    await bounded(db.initialize(mysqlTarget));
    blocked.release();
    await setImmediate();
    expect(handshakes).toBe(1);
    expect(routes).toEqual([{ transport: "direct", reason: "ETIMEDOUT" }]);
  } finally { blocked.release(); await db.close(); }
  expect(closed).toBe(1);
});

test.each(["mysql", "redis"] as const)("%s discards an injected handshake that succeeds after cancellation", async protocol => {
  const started = gate(), handshake = gate();
  const controller = new AbortController();
  const reason = new Error("cancel handshake");
  let closed = 0, published = 0;
  const transport: TcpTransport = { kind: "tcp", name: "direct", connect: async () => endpoint };
  const lifecycle = { signal: controller.signal, onRoute: () => { published++; } };
  let pending: Promise<unknown>, close: () => Promise<void>;
  if (protocol === "mysql") {
    const db = new MysqlDatabase([transport], { ...lifecycle, connectTimeoutMs: 30_000, queryTimeoutMs: 100 }, async () => {
      started.release(); await handshake.promise;
      return { destroy() {}, connection: { stream: { destroy() { closed++; } } } } as unknown as Connection;
    });
    pending = db.initialize(mysqlTarget); close = () => db.close();
  } else {
    const db = new RedisAccess(transport, { timeoutMs: 30_000, useSsl: false }, async () => {
      started.release(); await handshake.promise;
      return { endpoint, isReady: true, close() { closed++; }, ping: async () => "PONG", info: async () => ({}),
        dbSize: async () => 0, command: async () => null, pipeline: async () => [],
        scan: async () => ({ cursor: "0", keys: [] }) } satisfies RedisManagedConnection;
    }, lifecycle);
    pending = db.connection(endpoint, 0); close = () => db.close();
  }
  const settled = pending.catch(error => error);
  try {
    await started.promise;
    controller.abort(reason);
    expect(await bounded(settled)).toBe(reason);
    await bounded(close());
    handshake.release();
    await setImmediate();
    expect(closed).toBe(1);
    expect(published).toBe(0);
  } finally { handshake.release(); await close(); }
});

for (const runtime of ["node", "bun"]) test(`${runtime} releases native MySQL handshake sockets after cancellation`, () => {
  const result = spawnSync(runtime, [fileURLToPath(new URL("./fixtures/mysql-acquisition.mjs", import.meta.url))],
    { encoding: "utf8", timeout: 3000 });
  expect(result.error).toBeUndefined();
  expect(result.status, result.stderr).toBe(0);
  expect(result.stderr).toBe("");
  expect(result.stdout.trim()).toBe("disposed");
}, 5000);

test("cancellation at handshake delivery boundaries never loses or double-closes a MySQL connection", async () => {
  for (let turns = 0; turns < 8; turns++) {
    const controller = new AbortController();
    let closed = 0;
    const cancelAfter = (remaining: number) => queueMicrotask(() => {
      if (remaining) cancelAfter(remaining - 1);
      else controller.abort(new Error("cancel at delivery"));
    });
    const db = new MysqlDatabase([{ kind: "tcp", name: "direct", connect: async () => endpoint }],
      { signal: controller.signal, connectTimeoutMs: 1000, queryTimeoutMs: 100 }, async () => {
        cancelAfter(turns);
        return { destroy() {}, connection: { stream: { destroy() { closed++; } } } } as unknown as Connection;
      });
    await db.initialize(mysqlTarget).catch(error => { expect(error).toBe(controller.signal.reason); });
    await db.close();
    await setImmediate();
    expect(closed).toBe(1);
  }
});
