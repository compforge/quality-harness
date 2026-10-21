import { constants } from "node:fs";
import { open } from "node:fs/promises";
import { resolve } from "node:path";
import { clientKey, FileDataSource, type ClientManager, type FileReadOptions } from "@compforge/harness-common";
import { FileError } from "../errors";
import { BoundedFileClient, fileClientOptions, type FileClientOptions } from "./client";

export class LocalFileClient extends BoundedFileClient {
  protected async capture(signal: AbortSignal, limits: Pick<FileReadOptions, "maxBytes" | "timeoutMs">, receive: (bytes: Uint8Array) => void): Promise<void> {
    try {
      // Nonblocking open prevents a FIFO path from hanging before its type can be checked.
      const file = await open(this.path, constants.O_RDONLY | constants.O_NONBLOCK);
      try {
        signal.throwIfAborted();
        if (!(await file.stat()).isFile()) {
          throw new FileError(`Not a regular file: ${this.path}`, { kind: "unsupported_operation" });
        }
        const stream = file.createReadStream({ autoClose: false, signal, highWaterMark: Math.min(limits.maxBytes + 1, 64 * 1024) });
        for await (const bytes of stream) receive(bytes as Buffer);
      } finally { await file.close(); }
    } catch (cause) {
      signal.throwIfAborted();
      if (cause instanceof FileError) throw cause;
      const code = (cause as NodeJS.ErrnoException)?.code;
      throw new FileError(`Cannot read local file: ${this.path}`, {
        kind: code === "ENOENT" || code === "ENOTDIR" ? "resource_not_found"
          : code === "EACCES" || code === "EPERM" ? "permission_denied" : "operation_failed",
        code, cause,
      });
    }
  }
}

/** @spec Resolve relative paths once at declaration; reads reopen the path to observe replaced mounted files. */
export class LocalFileDataSource extends FileDataSource<LocalFileClient> {
  readonly path: string;
  readonly clientKey: string;
  readonly #options: Readonly<FileClientOptions>;

  constructor(path: string, options: FileClientOptions) {
    super();
    if (!path || path.includes("\0")) throw new FileError("File path must be nonempty and contain no NUL", { kind: "invalid_argument" });
    this.path = resolve(path);
    this.#options = fileClientOptions(options);
    this.clientKey = clientKey("local-file", { path: this.path, options: this.#options });
  }
  createClient(_clients: Pick<ClientManager, "get">, signal: AbortSignal): LocalFileClient {
    return new LocalFileClient(this.path, this.#options, signal);
  }
}
