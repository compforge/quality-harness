import type { Connection as NativeConnection, FieldPacket } from "mysql2";
import type { Connection } from "mysql2/promise";
import type { DatabaseQueryLimits, DatabaseQueryResult, DatabaseRow } from "./types";

export function validateQueryLimits(limits: DatabaseQueryLimits): void {
  for (const name of ["timeoutMs", "maxRows", "maxBytes"] as const) {
    const value = limits[name];
    if (!Number.isSafeInteger(value) || value < 1 || value > 2_147_483_647) {
      throw new Error(`${name} must be a positive integer <= 2147483647`);
    }
  }
}

/**
 * Owns a disposable session: setup, execution and cancellation cannot affect another borrower.
 * Caller must classify SQL and use a least-privilege identity. READ ONLY is not a SQL sandbox
 * (for example routines and temporary tables need additional caller policy).
 */
export async function queryReadonlySession(
  connection: Connection,
  sql: string,
  values: readonly unknown[],
  limits: DatabaseQueryLimits,
  signal?: AbortSignal,
): Promise<DatabaseQueryResult> {
  validateQueryLimits(limits);
  signal?.throwIfAborted();
  let timer: ReturnType<typeof setTimeout> | undefined;
  let abort: (() => void) | undefined;
  try {
    return await Promise.race([
      (async () => {
        // Pin literal interpretation; prepared protocol binds values without SQL interpolation.
        await connection.query("SET SESSION sql_mode = 'STRICT_TRANS_TABLES,NO_BACKSLASH_ESCAPES'");
        await connection.query(`SET SESSION max_execution_time = ${limits.timeoutMs}`);
        await connection.query("START TRANSACTION READ ONLY");
        signal?.throwIfAborted();
        return await collectRows(connection, sql, values, limits);
      })(),
      new Promise<never>((_resolve, reject) => {
        const fail = (reason: unknown) => { connection.destroy(); reject(reason); };
        abort = () => fail(signal?.reason ?? new Error("MySQL query cancelled"));
        signal?.addEventListener("abort", abort, { once: true });
        timer = setTimeout(() => fail(new Error("MySQL readonly query timed out")), limits.timeoutMs);
        if (signal?.aborted) abort();
      }),
    ]);
  } finally {
    if (timer) clearTimeout(timer);
    if (abort) signal?.removeEventListener("abort", abort);
    // Disconnect rolls back and also abandons unread rows on truncation. Never reuse this session.
    connection.destroy();
  }
}

function collectRows(
  connection: Connection, sql: string, values: readonly unknown[], limits: DatabaseQueryLimits,
): Promise<DatabaseQueryResult> {
  // mysql2's promise wrapper exposes its callback connection at runtime, but omits it in typings.
  // Callback execute without a callback emits rows instead of buffering the whole result set.
  const native = (connection as Connection & { connection: NativeConnection }).connection;
  return new Promise((resolve, reject) => {
    const result: DatabaseQueryResult = { rows: [], columns: [], bytes: 0, truncated: false };
    let settled = false;
    const finish = (truncation?: "rows" | "bytes") => {
      if (settled) return;
      settled = true;
      result.truncated = !!truncation;
      if (truncation) result.truncation = truncation;
      resolve(result);
    };
    const query = native.execute({ sql, values: [...values], timeout: limits.timeoutMs });
    query.on("fields", (fields: FieldPacket[]) => { result.columns = fields?.map(field => field.name) ?? []; });
    query.on("result", (row: DatabaseRow) => {
      if (settled) return;
      if (result.rows.length >= limits.maxRows) { finish("rows"); connection.destroy(); return; }
      try {
        const bytes = Buffer.byteLength(JSON.stringify(row));
        if (result.bytes + bytes > limits.maxBytes) { finish("bytes"); connection.destroy(); return; }
        result.rows.push(row);
        result.bytes += bytes;
      } catch (error) { settled = true; connection.destroy(); reject(error); }
    });
    query.on("error", error => { if (!settled) { settled = true; reject(error); } });
    query.on("end", () => finish());
  });
}
