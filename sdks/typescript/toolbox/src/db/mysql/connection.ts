import type { Duplex } from "node:stream";
import { createConnection, type ConnectionOptions } from "mysql2";
import type { Connection } from "mysql2/promise";

/** Keep the native handle available so cancellation can destroy an unfinished handshake. */
export async function connectMysql(options: ConnectionOptions, signal?: AbortSignal): Promise<Connection> {
  signal?.throwIfAborted();
  const native = createConnection(options);
  const connection = native.promise();
  let abort!: () => void;
  try {
    await new Promise<void>((resolve, reject) => {
      abort = () => { destroyMysqlConnection(connection); reject(signal!.reason); };
      signal?.addEventListener("abort", abort, { once: true });
      native.connect(error => error ? reject(error) : resolve());
      if (signal?.aborted) abort();
    });
    signal?.throwIfAborted();
    return connection;
  } catch (error) { destroyMysqlConnection(connection); throw error; }
  finally { signal?.removeEventListener("abort", abort); }
}

/** @why Disposal must release the socket even when the peer never finishes its TCP half-close. */
export function destroyMysqlConnection(connection: Connection): void {
  // mysql2 3.23 destroy() only calls stream.end(); its promise wrapper omits the native handle in typings.
  const native = (connection as Connection & { connection: { stream: Duplex } }).connection;
  try { connection.destroy(); }
  finally { native.stream.destroy(); }
}
