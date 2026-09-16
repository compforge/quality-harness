import { expect, test } from "bun:test";
import { EventEmitter } from "node:events";
import type { Connection } from "mysql2/promise";
import { MysqlDatabase } from "../src/mysql";
import { DirectTransport, PodPythonTransport } from "../src/transport";

const target = { host: "localhost", port: 3306, database: "app", user: "reader", password: "secret" };
const limits = { timeoutMs: 100, maxRows: 2, maxBytes: 100 };

function fixture(rows: unknown[], stall = false) {
  const statements: string[] = [];
  const executed: unknown[] = [];
  let destroyed = false;
  const connection = {
    query: async (sql: string) => { statements.push(sql); },
    execute: async () => [[{ normal: true }]],
    destroy: () => { destroyed = true; },
    connection: { execute: (options: unknown) => {
      executed.push(options);
      const events = new EventEmitter();
      queueMicrotask(() => {
        events.emit("fields", [{ name: "id" }]);
        for (const row of rows) { if (!destroyed) events.emit("result", row); }
        if (!destroyed && !stall) events.emit("end");
      });
      return events;
    } },
  } as unknown as Connection;
  return { connection, statements, executed, destroyed: () => destroyed };
}

test("readonly setup and bound values use an isolated session, preserving ordinary queries", async () => {
  const sessions: ReturnType<typeof fixture>[] = [];
  const db = new MysqlDatabase([new DirectTransport()], { connectTimeoutMs: 100, queryTimeoutMs: 100 }, async () => {
    const session = fixture([{ id: 1 }, { id: 2 }]); sessions.push(session); return session.connection;
  });
  await db.query(target, "SELECT 1", []);
  const result = await db.queryReadonly(target, "SELECT ?", ["quote':colon"], limits);
  expect(result.rows).toEqual([{ id: 1 }, { id: 2 }]);
  expect(result.columns).toEqual(["id"]);
  expect(result.truncated).toBe(false);
  expect(sessions[1]!.statements.at(-1)).toBe("START TRANSACTION READ ONLY");
  expect(sessions[1]!.executed[0]).toMatchObject({ sql: "SELECT ?", values: ["quote':colon"] });
  expect(sessions[1]!.destroyed()).toBe(true);
  expect(sessions[0]!.destroyed()).toBe(false);
  await db.close();
});

test("row and byte budgets stop retaining data before full buffering", async () => {
  for (const [rows, expected] of [
    [[{ id: 1 }, { id: 2 }, { id: 3 }], "rows"],
    [[{ id: "x".repeat(200) }], "bytes"],
  ] as const) {
    const session = fixture([...rows]);
    const db = new MysqlDatabase([new DirectTransport()], { connectTimeoutMs: 100, queryTimeoutMs: 100 }, async () => session.connection);
    const result = await db.queryReadonly(target, "SELECT id FROM t", [], limits);
    expect(result.truncation).toBe(expected);
    expect(result.rows.length).toBeLessThanOrEqual(limits.maxRows);
    expect(result.bytes).toBeLessThanOrEqual(limits.maxBytes);
    expect(session.destroyed()).toBe(true);
  }
});

test("deadline and abort destroy in-flight sessions without replay", async () => {
  for (const cancel of [false, true]) {
    const controller = new AbortController();
    const session = fixture([], true);
    const db = new MysqlDatabase([new DirectTransport()], { connectTimeoutMs: 100, queryTimeoutMs: 100, signal: controller.signal }, async () => session.connection);
    const pending = db.queryReadonly(target, "SELECT 1", [], { ...limits, timeoutMs: 10 });
    if (cancel) setTimeout(() => controller.abort(new Error("cancelled")), 1);
    await expect(pending).rejects.toThrow(cancel ? "cancelled" : "timed out");
    expect(session.destroyed()).toBe(true);
    expect(session.executed).toHaveLength(1);
  }
});

test("invalid limits and unsupported Pod route fail closed", async () => {
  let invoked = false;
  const db = new MysqlDatabase([new PodPythonTransport(async () => { invoked = true; return ""; })], { connectTimeoutMs: 100, queryTimeoutMs: 100 });
  expect(() => db.queryReadonly(target, "SELECT 1", [], { ...limits, maxRows: 0 })).toThrow("maxRows");
  await expect(db.queryReadonly(target, "SELECT 1", [], limits)).rejects.toThrow("native TCP");
  expect(invoked).toBe(false);
});
