import type { Client } from "./client.js";
import type { ClientManager } from "./client-manager.js";
import type { DataSource } from "./datasource.js";

export interface FileReadOptions {
  /** Maximum complete size of each file; an oversized file returns an error without partial bytes. */
  maxBytes: number;
  /** Maximum bytes retained across the whole read, including in-progress files. */
  maxTotalBytes: number;
  /** One deadline for queueing and acquiring the complete group. */
  timeoutMs: number;
  signal?: AbortSignal;
}

export type FileReadResult =
  | { readonly path: string; readonly status: "ok"; readonly bytes: Uint8Array }
  | { readonly path: string; readonly status: "error"; readonly error: Error };

/**
 * @spec Each read acquires the declared group once and returns one result per path, in declaration order.
 * Individual file failures do not discard other files; cancellation, the group deadline, total budget
 * and transport/protocol failures reject the operation. Every read obtains fresh bytes.
 * @rule Group acquisition is not an atomic filesystem snapshot. Parsing and evidence belong to consumers.
 */
export interface FileClient extends Client {
  read(options: FileReadOptions): Promise<readonly FileReadResult[]>;
}

/**
 * A nonempty group of distinct paths that the caller declares can be acquired together.
 * @why The source is the acquisition unit; grouping does not require consumers to guess transport capabilities.
 * @rule clientKey identifies access and the group, not cached contents or a Service-owned alias.
 * @see {@link component://../../../docs/toolbox.md}
 */
export abstract class FileDataSource<C extends FileClient = FileClient> implements DataSource<C> {
  abstract readonly paths: readonly string[];
  readonly description?: string;
  abstract readonly clientKey: string;
  abstract createClient(clients: Pick<ClientManager, "get">, signal: AbortSignal): C;
}
