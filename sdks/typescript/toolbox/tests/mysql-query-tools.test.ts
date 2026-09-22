import { expect, test } from "bun:test";
import { DataLoader } from "../src/data-loader";
import { withReadScope, limitedRows, limitedStatement, queryLimited,
  type Database, type DatabaseTarget } from "../src/mysql";

const target: DatabaseTarget = { host: "db", port: 3306, database: "app", user: "reader", password: "test" };
const options = () => ({ maxEntries: 10, maxBytes: 4096, signal: new AbortController().signal });
function fixture() {
  const calls: string[] = [];
  const database: Database = {
    query: async (_target, sql) => { calls.push(sql); return [{ sql }, { second: true }]; },
    queryOne: async (_target, sql) => { calls.push(`one:${sql}`); return { sql }; },
    queryBatch: async (_target, statements) => {
      calls.push(`batch:${statements.map(s => s.sql).join(",")}`);
      return statements.map(({ sql }) => [{ sql }]);
    },
    close: async () => { calls.push("close"); },
  };
  return { calls, database };
}

test("bounded reads validate LIMIT, bind business values and distinguish exact bounds from truncation", async () => {
  const statement = { sql: "SELECT id FROM items WHERE owner = ? ORDER BY id", values: ["' OR 1=1"] };
  const sql = limitedStatement(statement, 2);
  expect(sql).toEqual({ sql: `${statement.sql} LIMIT 3`, values: statement.values });
  expect(limitedRows([{ id: 1 }, { id: 2 }], 2).truncated).toBe(false);
  const result = await queryLimited({ query: async (_target, text, values) => {
    expect(text).toBe(sql.sql); expect(values).toEqual(statement.values);
    return [{ id: 1 }, { id: 2 }, { id: 3 }];
  } }, target, statement, 2);
  expect(result).toEqual({ rows: [{ id: 1 }, { id: 2 }], truncated: true });
  for (const invalid of [0, -1, 1.5, NaN, Infinity, 2_147_483_647]) {
    await expect(queryLimited({ query: async () => { throw new Error("must not query"); } }, target, statement, invalid))
      .rejects.toThrow("maxRows");
  }
});

test("scope adapter coalesces reads and separates identities, parameter types and queryOne", async () => {
  const { database, calls } = fixture();
  const scope = new DataLoader(options());
  const reads = withReadScope(database, scope);
  const [a, b] = await Promise.all([reads.query(target, "A", [1]), reads.query(target, "A", [1])]);
  expect(a).toBe(b);
  await reads.queryOne(target, "A", [1]);
  for (const identity of [{ ...target, user: "other" }, { ...target, password: "other" }, { ...target, database: "other" }]) {
    await reads.query(identity, "A", [1]);
  }
  for (const value of [null, undefined, 1n, "1", new Date(0), new Date(0).toISOString()]) {
    await reads.query(target, "A", [value]);
  }
  expect(calls).toHaveLength(11);
  await reads.close();
  expect(calls).not.toContain("close");
  await scope.close();
  await expect(reads.query(target, "A", [])).rejects.toThrow("closed");
});

test("native batches reuse hits, share duplicate misses and retain failures without replay", async () => {
  const { database, calls } = fixture();
  const scope = new DataLoader(options());
  const reads = withReadScope(database, scope);
  await reads.query(target, "A", []);
  const rows = await reads.queryBatch(target, ["B", "A", "B", "C"].map(sql => ({ sql, values: [] })));
  expect(calls).toEqual(["A", "batch:B,C"]);
  expect(rows.map(items => items[0]!.sql)).toEqual(["B", "A", "B", "C"]);
  expect(rows[0]).toBe(rows[2]);
  database.queryBatch = async () => { calls.push("failed"); throw new Error("SQL failed"); };
  for (let i = 0; i < 2; i++) await expect(reads.queryBatch(target, [{ sql: "bad", values: [] }])).rejects.toThrow("SQL failed");
  expect(calls.filter(x => x === "failed")).toHaveLength(1);
  await scope.close();
});

test("fallback batches are ordered and stop after a failure", async () => {
  const { database, calls } = fixture();
  delete database.queryBatch;
  database.query = async (_target, sql) => { calls.push(sql); if (sql === "bad") throw new Error("bad"); return [{ sql }]; };
  const scope = new DataLoader(options());
  const reads = withReadScope(database, scope);
  await expect(reads.queryBatch(target, ["ok", "bad", "later"].map(sql => ({ sql, values: [] })))).rejects.toThrow("bad");
  expect(calls).toEqual(["ok", "bad"]);
  await scope.close();
});

test("driver-specific parameters bypass caching without splitting a native batch", async () => {
  const { database, calls } = fixture();
  const scope = new DataLoader(options());
  const reads = withReadScope(database, scope);
  const statements = [{ sql: "custom", values: [{ toSqlString: () => "CURRENT_TIMESTAMP" }] }, { sql: "ordinary", values: [1] }];
  await reads.queryBatch(target, statements);
  await reads.queryBatch(target, statements);
  expect(calls).toEqual(["batch:custom,ordinary", "batch:custom"]);
  await scope.close();
});

test("closing scope stops fallback batch before sending remaining statements", async () => {
  const { database, calls } = fixture();
  delete database.queryBatch;
  const scope = new DataLoader(options());
  database.query = async (_target, sql) => {
    calls.push(sql);
    void scope.close();
    return [{ sql }];
  };
  const reads = withReadScope(database, scope);
  await expect(reads.queryBatch(target, ["first", "later"].map(sql => ({ sql, values: [] })))).rejects.toThrow("closed");
  await scope.close();
  expect(calls).toEqual(["first"]);
});
