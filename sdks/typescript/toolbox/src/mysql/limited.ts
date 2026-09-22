import type { Database, DatabaseRow, DatabaseTarget, SqlStatement } from "./types";

export interface LimitedRows { rows: DatabaseRow[]; truncated: boolean }

function validateLimit(maxRows: number): void {
  if (!Number.isSafeInteger(maxRows) || maxRows < 1 || maxRows >= 2_147_483_647) {
    throw new Error("maxRows must be a positive integer < 2147483647");
  }
}

/** Append to caller-owned SQL without an existing LIMIT or trailing terminator; never rewrite SQL.
 * An integer literal avoids driver/server differences in numeric LIMIT parameter binding.
 */
export function limitedStatement(statement: SqlStatement, maxRows: number): SqlStatement {
  validateLimit(maxRows);
  return { sql: `${statement.sql} LIMIT ${maxRows + 1}`, values: statement.values };
}

/** The extra row proves truncation; batch callers can use the same projection. */
export function limitedRows(rows: DatabaseRow[], maxRows: number): LimitedRows {
  validateLimit(maxRows);
  return { rows: rows.slice(0, maxRows), truncated: rows.length > maxRows };
}

/** Row-count bound for trusted reads on the existing session, not a readonly transaction or byte cap. */
export async function queryLimited(
  database: Pick<Database, "query">, target: DatabaseTarget, statement: SqlStatement, maxRows: number,
): Promise<LimitedRows> {
  const bounded = limitedStatement(statement, maxRows);
  return limitedRows(await database.query(target, bounded.sql, bounded.values), maxRows);
}
