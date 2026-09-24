import type { Duplex } from "node:stream";
import type { Connection } from "mysql2/promise";

/** @why Disposal must release the socket even when the peer never finishes its TCP half-close. */
export function destroyMysqlConnection(connection: Connection): void {
  // mysql2 3.23 destroy() only calls stream.end(); its promise wrapper omits the native handle in typings.
  const native = (connection as Connection & { connection: { stream: Duplex } }).connection;
  try { connection.destroy(); }
  finally { native.stream.destroy(); }
}
