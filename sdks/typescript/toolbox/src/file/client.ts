import type { FileClient, FileReadOptions, FileReadResult } from "@compforge/harness-common";
import { ConcurrencyPool } from "../concurrency";
import { FileError, ToolboxError } from "../errors";

export interface FileClientOptions { concurrency: number }
export interface FileCaptureSink {
  receive(index: number, bytes: Uint8Array): void;
  complete(index: number, error?: Error): void;
}

export function fileClientOptions(options: FileClientOptions): Readonly<FileClientOptions> {
  if (!Number.isSafeInteger(options.concurrency) || options.concurrency < 1) {
    throw new FileError("File concurrency must be a positive integer", { kind: "invalid_argument" });
  }
  return Object.freeze({ concurrency: options.concurrency });
}

export function filePaths(paths: readonly string[]): readonly string[] {
  if (!paths.length || paths.some(path => typeof path !== "string" || !path || path.includes("\0"))
    || new Set(paths).size !== paths.length) {
    throw new FileError("File paths must be nonempty, distinct and contain no NUL", { kind: "invalid_argument" });
  }
  return Object.freeze([...paths]);
}

/** Group lifecycle and budgets; a per-file failure releases its partial bytes and preserves sibling results. */
export abstract class BoundedFileClient implements FileClient {
  readonly paths: readonly string[];
  readonly #controller = new AbortController();
  readonly #signal: AbortSignal;
  readonly #pool: ConcurrencyPool;
  readonly #operations = new Set<Promise<readonly FileReadResult[]>>();
  #ready = false;
  #disposal?: Promise<void>;

  constructor(paths: readonly string[], options: FileClientOptions, signal: AbortSignal) {
    this.paths = filePaths(paths);
    this.#pool = new ConcurrencyPool(fileClientOptions(options).concurrency);
    this.#signal = AbortSignal.any([signal, this.#controller.signal]);
  }
  async initialize(): Promise<void> {
    this.#signal.throwIfAborted();
    await this.initializeAccess();
    this.#signal.throwIfAborted();
    this.#ready = true;
  }
  protected async initializeAccess(): Promise<void> {}
  protected abstract capture(signal: AbortSignal, limits: FileReadOptions, sink: FileCaptureSink): Promise<void>;

  /** @spec Enforce budgets while streaming; file failures discard only that file, never publish partial bytes. */
  async read(options: FileReadOptions): Promise<readonly FileReadResult[]> {
    this.#signal.throwIfAborted();
    if (!this.#ready) throw new FileError("File client is not initialized", { kind: "operation_failed" });
    if (!Number.isSafeInteger(options.maxBytes) || options.maxBytes < 0 || options.maxBytes >= Number.MAX_SAFE_INTEGER - 5
      || !Number.isSafeInteger(options.maxTotalBytes) || options.maxTotalBytes < 0
      || !Number.isSafeInteger(options.timeoutMs) || options.timeoutMs <= 0 || options.timeoutMs > 2_147_483_647) {
      throw new FileError("Invalid file group read limits", { kind: "invalid_argument" });
    }
    const controller = new AbortController();
    const signal = AbortSignal.any([this.#signal, controller.signal, ...(options.signal ? [options.signal] : [])]);
    const timer = setTimeout(() => controller.abort(new FileError("File group read timed out", { kind: "timeout" })), options.timeoutMs);
    const operation = this.#pool.run(async () => {
      const files = this.paths.map(path => ({ path, chunks: [] as Uint8Array[], size: 0, done: false, error: undefined as Error | undefined }));
      let retained = 0;
      const discard = (file: typeof files[number], error: Error) => {
        retained -= file.size; file.size = 0; file.chunks = []; file.error ??= error;
      };
      const entry = (index: number) => {
        const file = files[index];
        if (!file || file.done) throw new FileError("Invalid file group response", { kind: "invalid_response" });
        return file;
      };
      const sink: FileCaptureSink = {
        receive: (index, bytes) => {
          if (signal.aborted) return;
          const file = entry(index);
          if (file.error) return;
          if (bytes.byteLength > options.maxBytes - file.size) {
            discard(file, new FileError(`File exceeds ${options.maxBytes} bytes: ${file.path}`, { kind: "limit_exceeded" }));
          } else if (bytes.byteLength > options.maxTotalBytes - retained) {
            controller.abort(new FileError(`File group exceeds ${options.maxTotalBytes} bytes`, { kind: "limit_exceeded" }));
          } else {
            file.chunks.push(Uint8Array.from(bytes)); file.size += bytes.byteLength; retained += bytes.byteLength;
          }
        },
        complete: (index, error) => {
          if (signal.aborted) return;
          const file = entry(index);
          if (error) discard(file, error);
          file.done = true;
        },
      };
      await this.capture(signal, options, sink);
      signal.throwIfAborted();
      if (files.some(file => !file.done)) throw new FileError("Incomplete file group response", { kind: "invalid_response" });
      return files.map((file): FileReadResult => {
        if (file.error) return { path: file.path, status: "error", error: file.error };
        const bytes = new Uint8Array(file.size);
        let offset = 0;
        for (const chunk of file.chunks) { bytes.set(chunk, offset); offset += chunk.byteLength; }
        return { path: file.path, status: "ok", bytes };
      });
    }, signal);
    this.#operations.add(operation);
    try { return await operation; }
    catch (cause) {
      signal.throwIfAborted();
      if (cause instanceof ToolboxError) throw cause;
      throw new FileError("Cannot acquire file group", { kind: "operation_failed", cause });
    } finally {
      clearTimeout(timer); this.#operations.delete(operation);
    }
  }
  dispose(): Promise<void> {
    return this.#disposal ??= (async () => {
      this.#controller.abort(new Error("File client disposed"));
      await Promise.allSettled(this.#operations);
    })();
  }
}
