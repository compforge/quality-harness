import { expect, test } from "bun:test";
import { createConnection, type Connection } from "mysql2/promise";
import { createServer } from "node:net";
import { ToolboxError } from "../src/errors";
import { MysqlDatabase, type DatabaseTarget } from "../src/mysql";
import { DirectTransport, PodPythonTransport, PortForwardTransport } from "../src/transport";
import { preparePodSql } from "../src/mysql/pod";

const target: DatabaseTarget = { host: "rds.example", port: 3306, database: "db", user: "reader", password: "stdin-only-password" };
const options = { connectTimeoutMs: 100, queryTimeoutMs: 500 };
function networkError(code = "ECONNREFUSED") { return Object.assign(new Error("unavailable"), { code }); }

test("host connection failure selects Pod without changing identity; subsequent queries reuse route", async () => {
  let connects = 0;
  const calls: { command: readonly string[]; stdin: string }[] = [];
  const routes: unknown[] = [];
  const pod = new PodPythonTransport(async (command, options) => {
    calls.push({ command, stdin: options.stdin });
    return '{"rows":[{"count":"9007199254740993"}]}';
  });
  const db = new MysqlDatabase([new DirectTransport(), pod], { ...options, onRoute: route => routes.push(route) }, async () => { connects++; throw networkError(); });
  expect(await db.queryOne(target, "SELECT ?", ["x' OR 1=1 --"])).toEqual({ count: "9007199254740993" });
  await db.query(target, "SELECT 1", []);
  expect(connects).toBe(1);
  expect(routes).toEqual([{ transport: "pod-python", reason: "ECONNREFUSED" }]);
  const request = JSON.parse(calls[0]!.stdin);
  expect(request.target).toEqual(target);
  expect(request.sql).toBe("SELECT %s");
  expect(request.values).toEqual(["x' OR 1=1 --"]);
  expect(calls[0]!.command.join(" ")).not.toContain(target.password);
  expect(calls[0]!.command.join(" ")).not.toContain("x' OR 1=1 --");
  await db.close();
});

test("SQL failure is not retried over another transport; failed native socket is discarded", async () => {
  let destroys = 0;
  let attempts = 0;
  const db = new MysqlDatabase([new DirectTransport(), new PodPythonTransport(async () => { throw new Error("unexpected Pod retry"); })], options, async () => {
    attempts++;
    return { execute: async () => { throw networkError("ECONNRESET"); }, connection: { stream: { destroy: () => { destroys++; } } }, destroy: () => {} } as unknown as Connection;
  });
  await expect(db.query(target, "SELECT 1", [])).rejects.toThrow("unavailable");
  await expect(db.query(target, "SELECT 1", [])).rejects.toThrow("unavailable");
  expect(attempts).toBe(2);
  expect(destroys).toBe(2);
});

test("authentication errors and cancellation never trigger Pod fallback", async () => {
  const pod = new PodPythonTransport(async () => { throw new Error("unexpected Pod retry"); });
  const denied = new MysqlDatabase([new DirectTransport(), pod], options, async () => { throw networkError("ER_ACCESS_DENIED_ERROR"); });
  await expect(denied.query(target, "SELECT 1", [])).rejects.toThrow("unavailable");
  const controller = new AbortController();
  const cancelled = new MysqlDatabase([new DirectTransport(), pod], { ...options, signal: controller.signal }, async () => {
    controller.abort(new Error("cancelled"));
    throw networkError();
  });
  await expect(cancelled.query(target, "SELECT 1", [])).rejects.toThrow("cancelled");
});

test("Pod SQL keeps literals/comments and percent signs; protocol binding owns values", () => {
  expect(preparePodSql("SELECT '?', `?`, '100%', ? /* ? */ -- ?\n, ?", [1, null]))
    .toBe("SELECT '?', `?`, '100%%', %s /* ? */ -- ?\n, %s");
  expect(() => preparePodSql("SELECT ?", [])).toThrow("count");
  expect(() => preparePodSql("SELECT 1", [1])).toThrow("placeholder");
});

test("close destroys open sockets and drops cached sessions", async () => {
  let destroyed = 0;
  let opened = 0;
  const db = new MysqlDatabase([new DirectTransport()], options, async () => {
    opened++;
    return { execute: async () => [[{ ok: 1 }]], connection: { stream: { destroy: () => { destroyed++; } } }, destroy: () => {} } as unknown as Connection;
  });
  await db.query(target, "SELECT 1", []);
  await db.close();
  await db.query(target, "SELECT 1", []);
  await db.close();
  expect(opened).toBe(2);
  expect(destroyed).toBe(2);
});

test("shared MySQL client queues Pod queries and drains before close", async () => {
  let active = 0;
  let peak = 0;
  let connects = 0;
  const pod = new PodPythonTransport(async (_command, run) => {
    peak = Math.max(peak, ++active);
    await Promise.resolve();
    active--;
    return JSON.stringify({ rows: [{ id: JSON.parse(run.stdin).values[0] }] });
  });
  const db = new MysqlDatabase([new DirectTransport(), pod], options, async () => { connects++; throw networkError(); });
  const queries = [1, 2, 3].map(id => db.queryOne(target, "SELECT ?", [id]));
  const close = db.close();
  expect(await Promise.all(queries)).toEqual([{ id: 1 }, { id: 2 }, { id: 3 }]);
  await close;
  expect(connects).toBe(1);
  expect(peak).toBe(1);
});

test("Pod batch runs independent statements in one exec and preserves order", async () => {
  const calls: { command: readonly string[]; stdin: string }[] = [];
  const pod = new PodPythonTransport(async (command, options) => {
    calls.push({ command, stdin: options.stdin });
    const request = JSON.parse(options.stdin);
    return JSON.stringify({ results: request.statements.map((statement: { values: unknown[] }) => ({ rows: [{ id: statement.values[0] }] })) });
  });
  const db = new MysqlDatabase([new DirectTransport(), pod], options, async () => { throw networkError(); });
  const results = await db.queryBatch(target, [
    { sql: "SELECT ? /* keep % literal */", values: [1] },
    { sql: "SELECT ?", values: [2] },
  ]);
  expect(results).toEqual([[{ id: 1 }], [{ id: 2 }]]);
  expect(calls.length).toBe(1);
  const request = JSON.parse(calls[0]!.stdin);
  expect(request.sql).toBeUndefined();
  expect(request.statements.map((statement: { sql: string }) => statement.sql)).toEqual(["SELECT %s /* keep %% literal */", "SELECT %s"]);
  expect(calls[0]!.command.join(" ")).not.toContain(target.password);
  await db.close();
});

test("TCP batch reuses one native session and fails fast without transport retry", async () => {
  const executed: string[] = [];
  let destroys = 0;
  let attempts = 0;
  const connection = {
    execute: async ({ sql }: { sql: string }) => {
      executed.push(sql);
      if (sql === "SELECT bad") throw networkError("ECONNRESET");
      return [[{ sql }]];
    },
    connection: { stream: { destroy: () => { destroys++; } } }, destroy: () => {},
  } as unknown as Connection;
  const db = new MysqlDatabase([new DirectTransport(), new PodPythonTransport(async () => { throw new Error("unexpected Pod retry"); })], options, async () => { attempts++; return connection; });
  expect(await db.queryBatch(target, [{ sql: "SELECT 1", values: [] }, { sql: "SELECT 2", values: [] }]))
    .toEqual([[{ sql: "SELECT 1" }], [{ sql: "SELECT 2" }]]);
  await expect(db.queryBatch(target, [{ sql: "SELECT 3", values: [] }, { sql: "SELECT bad", values: [] }, { sql: "SELECT 4", values: [] }]))
    .rejects.toThrow("unavailable");
  expect(executed).toEqual(["SELECT 1", "SELECT 2", "SELECT 3", "SELECT bad"]);
  expect(attempts).toBe(1);
  expect(destroys).toBe(1);
  expect(await db.queryBatch(target, [])).toEqual([]);
  await db.close();
});

test("Pod batch error aborts the batch and reports the statement class only", async () => {
  const pod = new PodPythonTransport(async () => JSON.stringify({ error: "OperationalError", code: 1146 }));
  const db = new MysqlDatabase([new DirectTransport(), pod], options, async () => { throw networkError(); });
  await expect(db.queryBatch(target, [{ sql: "SELECT 1", values: [] }])).rejects.toThrow("batch failed: OperationalError (1146)");
  await db.close();
});


test("mysql2 handshake close selects the next TCP transport and reuses its session", async () => {
  const server = createServer(socket => socket.end());
  await new Promise<void>(resolve => server.listen(0, "127.0.0.1", resolve));
  const port = (server.address() as { port: number }).port;
  const routes: unknown[] = [];
  let connects = 0;
  let executed = 0;
  const connection = {
    execute: async () => { executed++; return [[{ ok: 1 }]]; },
    connection: { stream: { destroy: () => {} } }, destroy: () => {},
  } as unknown as Connection;
  const db = new MysqlDatabase([
    new PortForwardTransport(async () => ({ host: "127.0.0.1", port })), new DirectTransport(),
  ], { ...options, connectTimeoutMs: 1000, onRoute: route => routes.push(route) }, async config => {
    connects++;
    return config.host === "127.0.0.1" ? createConnection(config) : connection;
  });
  try {
    await db.initialize(target);
    expect(executed).toBe(0);
    expect(await db.queryOne(target, "SELECT 1", [])).toEqual({ ok: 1 });
    expect(await db.queryOne(target, "SELECT 1", [])).toEqual({ ok: 1 });
    expect(connects).toBe(2);
    expect(executed).toBe(2);
    expect(routes).toEqual([{ transport: "direct", reason: "PROTOCOL_CONNECTION_LOST" }]);
  } finally {
    await db.close();
    await new Promise<void>(resolve => server.close(() => resolve()));
  }
});

test.each(["initialize", "query"] as const)("exhausted connection routes report connect phase through %s", async operation => {
  const cause = Object.assign(new Error("private driver details"), { code: "PROTOCOL_CONNECTION_LOST" });
  let connects = 0;
  const db = new MysqlDatabase([new DirectTransport(), new DirectTransport()], options, async () => { connects++; throw cause; });
  try {
    const result = operation === "initialize" ? db.initialize(target) : db.query(target, "SELECT 1", []);
    const error = await result.catch(error => error);
    expect(error).toBeInstanceOf(ToolboxError);
    expect(error).toMatchObject({ kind: "connection_lost", code: "PROTOCOL_CONNECTION_LOST", cause });
    expect(error.message).toBe("MySQL connect failed: connection lost (PROTOCOL_CONNECTION_LOST)");
    expect(error.message).not.toContain("private driver details");
    expect(connects).toBe(2);
  } finally { await db.close(); }
});

test.each(["query", "queryBatch", "queryReadonly"] as const)("%s disconnect reports query phase without replay", async operation => {
  const cause = Object.assign(new Error("private SQL and bound values"), { code: "PROTOCOL_CONNECTION_LOST" });
  let connects = 0;
  let executed = 0;
  let fallback = 0;
  let destroyed = 0;
  const fail = async () => { executed++; throw cause; };
  const connection = {
    query: fail, execute: fail,
    connection: { stream: { destroy: () => { destroyed++; } } }, destroy: () => {},
  } as unknown as Connection;
  const db = new MysqlDatabase([
    new DirectTransport(), new PortForwardTransport(async () => { fallback++; throw new Error("unexpected fallback"); }),
  ], options, async () => { connects++; return connection; });
  try {
    const result = operation === "queryBatch"
      ? db.queryBatch(target, [{ sql: "SELECT 1", values: [] }, { sql: "SELECT 2", values: [] }])
      : operation === "queryReadonly"
        ? db.queryReadonly(target, "SELECT 1", [], { timeoutMs: 500, maxRows: 1, maxBytes: 100 })
        : db.query(target, "SELECT 1", []);
    const error = await result.catch(error => error);
    expect(error).toBeInstanceOf(ToolboxError);
    expect(error).toMatchObject({ kind: "connection_lost", code: "PROTOCOL_CONNECTION_LOST", cause });
    expect(error.message).toBe("MySQL query failed: connection lost (PROTOCOL_CONNECTION_LOST)");
    expect(error.message).not.toContain("private SQL");
    expect(connects).toBe(1);
    expect(executed).toBe(1);
    expect(fallback).toBe(0);
    expect(destroyed).toBeGreaterThan(0);
  } finally { await db.close(); }
});
