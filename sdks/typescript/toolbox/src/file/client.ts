import type { FileClient, FileReadOptions } from "@compforge/harness-common";
import { ConcurrencyPool } from "../concurrency";
import { FileError, ToolboxError } from "../errors";

export interface FileClientOptions { concurrency: number }

export function fileClientOptions(options: FileClientOptions): Readonly<FileClientOptions> {
  if (!Number.isSafeInteger(options.concurrency) || options.concurrency < 1) {
    throw new FileError("File concurrency must be a positive integer", { kind: "invalid_argument" });
  }
  return Object.freeze({ concurrency: options.concurrency });
}

/** Shared limits and lifecycle; transports stream bytes and own per-read resource cleanup. */
export abstract class BoundedFileClient implements FileClient {
  readonly #controller = new AbortController();
  readonly #signal: AbortSignal;
  readonly #pool: ConcurrencyPool;
  readonly #operations = new Set<Promise<Uint8Array>>();
  #ready = false;
  #disposal?: Promise<void>;

  constructor(readonly path: string, options: FileClientOptions, signal: AbortSignal) {
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
  protected abstract capture(signal: AbortSignal, limits: Pick<FileReadOptions, "maxBytes" | "timeoutMs">, receive: (bytes: Uint8Array) => void): Promise<void>;

  /** @spec Limits apply while streaming; a failed read never publishes partial bytes. */
  async read(options: FileReadOptions): Promise<Uint8Array> {
    this.#signal.throwIfAborted();
    if (!this.#ready) throw new FileError(`File client is not initialized: ${this.path}`, { kind: "operation_failed" });
    if (!Number.isSafeInteger(options.maxBytes) || options.maxBytes < 0 || options.maxBytes === Number.MAX_SAFE_INTEGER
      || !Number.isSafeInteger(options.timeoutMs) || options.timeoutMs <= 0 || options.timeoutMs > 2_147_483_647) {
      throw new FileError(`Invalid file read limits: ${this.path}`, { kind: "invalid_argument" });
    }
    const { maxBytes, timeoutMs } = options;
    const controller = new AbortController();
    const signal = AbortSignal.any([this.#signal, controller.signal, ...(options.signal ? [options.signal] : [])]);
    const timer = setTimeout(() => controller.abort(new FileError(`File read timed out: ${this.path}`, { kind: "timeout" })), timeoutMs);
    const operation = this.#pool.run(async () => {
      const chunks: Uint8Array[] = [];
      let size = 0;
      await this.capture(signal, { maxBytes, timeoutMs }, bytes => {
        if (signal.aborted) return;
        if (bytes.byteLength > maxBytes - size) {
          controller.abort(new FileError(`File exceeds ${maxBytes} bytes: ${this.path}`, { kind: "limit_exceeded" }));
          return;
        }
        chunks.push(Uint8Array.from(bytes));
        size += bytes.byteLength;
      });
      signal.throwIfAborted();
      const result = new Uint8Array(size);
      let offset = 0;
      for (const chunk of chunks) { result.set(chunk, offset); offset += chunk.byteLength; }
      return result;
    }, signal);
    this.#operations.add(operation);
    try { return await operation; }
    catch (cause) {
      signal.throwIfAborted();
      if (cause instanceof ToolboxError) throw cause;
      throw new FileError(`Cannot read file: ${this.path}`, { kind: "operation_failed", cause });
    } finally {
      clearTimeout(timer);
      this.#operations.delete(operation);
    }
  }

  dispose(): Promise<void> {
    return this.#disposal ??= (async () => {
      this.#controller.abort(new Error(`File client disposed: ${this.path}`));
      await Promise.allSettled(this.#operations);
    })();
  }
}
