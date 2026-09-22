import { serialize } from "node:v8";

export interface DataLoaderOptions {
  maxEntries: number;
  /** Serialized result bytes retained, excluding keys/errors and active operation memory. */
  maxBytes: number;
  signal: AbortSignal;
}

/** A scoped async loading cache, similar to Guava's LoadingCache.
 * Shares results, failures and in-flight reads until close; consumers must not mutate results.
 * Unlike a long-lived LoadingCache, this scope has no refresh/expiry or automatic batching.
 * Keys and loading operations belong to adapters; the same key must have the same result type.
 * Operations must honor their signal for prompt cancellation. No underlying client is owned.
 */
export class DataLoader {
  readonly #entries = new Map<unknown, Promise<unknown>>();
  readonly #pending = new Set<Promise<unknown>>();
  readonly #controller = new AbortController();
  #bytes = 0;
  #closing?: Promise<void>;

  constructor(private readonly options: DataLoaderOptions) {
    for (const name of ["maxEntries", "maxBytes"] as const) {
      if (!Number.isSafeInteger(options[name]) || options[name] < 1) throw new Error(`${name} must be a positive safe integer`);
    }
    options.signal.throwIfAborted();
    options.signal.addEventListener("abort", this.#abort, { once: true });
  }

  #abort = (): void => { void this.close(this.options.signal.reason); };

  /** Undefined keys bypass caching. A waiter's signal cancels only that wait, not shared work. */
  async read<T>(key: unknown, operation: (signal: AbortSignal) => Promise<T>, signal?: AbortSignal): Promise<T> {
    this.#controller.signal.throwIfAborted();
    signal?.throwIfAborted();
    let pending = key === undefined ? undefined : this.#entries.get(key);
    if (!pending) {
      const retain = key !== undefined && this.#entries.size < this.options.maxEntries;
      pending = Promise.resolve().then(async () => {
        this.#controller.signal.throwIfAborted();
        const value = await operation(this.#controller.signal);
        this.#controller.signal.throwIfAborted();
        if (retain) {
          let bytes: number;
          try { bytes = serialize(value).byteLength; } catch { bytes = Infinity; }
          if (this.#bytes + bytes <= this.options.maxBytes) this.#bytes += bytes;
          else this.#entries.delete(key);
        }
        return value;
      });
      if (retain) this.#entries.set(key, pending);
      this.#pending.add(pending);
      const done = () => { this.#pending.delete(pending!); };
      // Retain rejections too, matching Python's single-round observation semantics.
      void pending.then(done, done);
    }
    if (!signal) return await pending as T;
    return new Promise<T>((resolve, reject) => {
      const abort = () => { cleanup(); reject(signal.reason); };
      const cleanup = () => { signal.removeEventListener("abort", abort); };
      signal.addEventListener("abort", abort, { once: true });
      void pending.then(value => { cleanup(); resolve(value as T); }, error => { cleanup(); reject(error); });
    });
  }

  /** Abort cooperative operations and join all reads, including capacity-bypass reads. */
  close(reason: unknown = new Error("DataLoader scope is closed")): Promise<void> {
    if (!this.#closing) {
      this.#closing = Promise.resolve().then(async () => {
        await Promise.allSettled([...this.#pending]);
        this.#entries.clear();
        this.#bytes = 0;
      });
      this.options.signal.removeEventListener("abort", this.#abort);
      this.#controller.abort(reason);
      this.#entries.clear();
    }
    return this.#closing;
  }
}
