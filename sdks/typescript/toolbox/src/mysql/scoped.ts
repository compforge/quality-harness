import { createHash } from "node:crypto";
import { serialize } from "node:v8";
import type { DataLoader } from "../data-loader";
import type { Database, DatabaseRow, DatabaseTarget, SqlStatement } from "./types";

function queryKey(kind: string, target: DatabaseTarget, statement: SqlStatement): string | undefined {
  // Include credentials and parameter types in an opaque key. JSON conflates dates/strings,
  // undefined/null and buffers/objects, and cannot encode bigint.
  try {
    return createHash("sha256").update(serialize([
      kind, target.host, target.port, target.database, target.user, target.password,
      statement.sql, statement.values,
    ])).digest("hex");
  } catch {
    // Driver-specific parameter objects (e.g. functions) remain executable but are not cached.
    return undefined;
  }
}

/** Bind caller-approved reads to a borrowed scope. Results are shared and must not be mutated.
 * Close is a no-op: the root owns both scope and database lifetimes. Database operations retain
 * their client's cancellation/timeout policy; they cannot be individually aborted by this adapter.
 */
export function withReadScope(database: Database, scope: DataLoader): Database & Required<Pick<Database, "queryBatch">> {
  const query = (target: DatabaseTarget, sql: string, values: readonly unknown[]) =>
    scope.read(queryKey("query", target, { sql, values }), () => database.query(target, sql, values));
  return {
    query,
    queryOne: (target, sql, values) => scope.read(queryKey("one", target, { sql, values }),
      () => database.queryOne(target, sql, values)),
    queryBatch: async (target, statements) => {
      const misses: SqlStatement[] = [];
      let batch: Promise<DatabaseRow[][]> | undefined;
      // An empty batch still verifies scope lifetime without retaining a cache entry.
      if (!statements.length) return scope.read(undefined, async () => []);
      return Promise.all(statements.map(statement => scope.read(queryKey("query", target, statement), signal => {
        const position = misses.length;
        misses.push(statement);
        // Every miss enters before this microtask. Native and fallback batches preserve order.
        batch ??= Promise.resolve().then(async () => {
          signal.throwIfAborted();
          if (database.queryBatch) return database.queryBatch(target, misses);
          const rows: DatabaseRow[][] = [];
          for (const item of misses) {
            signal.throwIfAborted();
            rows.push(await database.query(target, item.sql, item.values));
          }
          return rows;
        });
        return batch.then(rows => rows[position]!);
      })));
    },
    close: async () => {},
  };
}
