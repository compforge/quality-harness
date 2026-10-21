import { constants } from "node:fs";
import { open } from "node:fs/promises";
import { resolve } from "node:path";
import { clientKey, FileDataSource, type ClientManager, type FileReadOptions } from "@compforge/harness-common";
import { FileError } from "../errors";
import { BoundedFileClient, fileClientOptions, filePaths, type FileClientOptions, type FileCaptureSink } from "./client";

export class LocalFileClient extends BoundedFileClient {
  protected async capture(signal: AbortSignal, limits: FileReadOptions, sink: FileCaptureSink): Promise<void> {
    for (const [index, path] of this.paths.entries()) {
      try {
        signal.throwIfAborted();
        // Nonblocking open prevents a FIFO from hanging before its type can be checked.
        const file = await open(path, constants.O_RDONLY | constants.O_NONBLOCK);
        try {
          signal.throwIfAborted();
          if (!(await file.stat()).isFile()) throw new FileError(`Not a regular file: ${path}`, { kind: "unsupported_operation" });
          const stream = file.createReadStream({ autoClose: false, signal, end: limits.maxBytes,
            highWaterMark: Math.min(limits.maxBytes + 1, 64 * 1024) });
          for await (const bytes of stream) sink.receive(index, bytes as Buffer);
        } finally { await file.close(); }
        sink.complete(index);
      } catch (cause) {
        signal.throwIfAborted();
        const code = (cause as NodeJS.ErrnoException)?.code;
        sink.complete(index, cause instanceof FileError ? cause : new FileError(`Cannot read local file: ${path}`, {
          kind: code === "ENOENT" || code === "ENOTDIR" ? "resource_not_found"
            : code === "EACCES" || code === "EPERM" ? "permission_denied" : "operation_failed",
          code, cause,
        }));
      }
    }
  }
}

/** @spec Resolve paths once at declaration; each group read reopens them to observe replaced mounted files. */
export class LocalFileDataSource extends FileDataSource<LocalFileClient> {
  readonly paths: readonly string[];
  readonly clientKey: string;
  readonly #options: Readonly<FileClientOptions>;
  constructor(paths: readonly string[], options: FileClientOptions) {
    super();
    this.paths = filePaths(filePaths(paths).map(path => resolve(path)));
    this.#options = fileClientOptions(options);
    this.clientKey = clientKey("local-file", { paths: this.paths, options: this.#options });
  }
  createClient(_clients: Pick<ClientManager, "get">, signal: AbortSignal): LocalFileClient {
    return new LocalFileClient(this.paths, this.#options, signal);
  }
}
