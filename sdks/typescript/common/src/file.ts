import type { Client } from "./client.js";
import type { ClientManager } from "./client-manager.js";
import type { DataSource } from "./datasource.js";

export interface FileReadOptions {
  /** Maximum complete file size; exceeding it rejects instead of returning a prefix. */
  maxBytes: number;
  /** Includes time waiting for a concurrency slot. */
  timeoutMs: number;
  signal?: AbortSignal;
}

/**
 * @spec Each read obtains fresh bytes from the source's bound path. Empty files succeed;
 * missing files, incomplete reads and exceeded limits reject.
 * @rule Parsing, configuration precedence and evidence persistence belong to consumers.
 */
export interface FileClient extends Client {
  read(options: FileReadOptions): Promise<Uint8Array>;
}

/**
 * A single file location, discoverable without creating a client or performing I/O.
 * @why A shared runtime type lets consumers recognize file sources without a second kind registry.
 * @rule clientKey identifies access, not cached file contents or a Service-owned alias.
 * @see {@link component://../../../docs/toolbox.md}
 */
export abstract class FileDataSource<C extends FileClient = FileClient> implements DataSource<C> {
  abstract readonly path: string;
  readonly description?: string;
  abstract readonly clientKey: string;
  abstract createClient(clients: Pick<ClientManager, "get">, signal: AbortSignal): C;
}
