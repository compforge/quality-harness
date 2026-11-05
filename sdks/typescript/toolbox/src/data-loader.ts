import { serialize } from "node:v8";

export interface DataLoaderOptions {
  maxEntries: number;
  /** Serialized result bytes retained, excluding keys/errors and active operation memory. */
  maxBytes: number;
  signal: AbortSignal;
  /** Non-sensitive label supplied by the adapter; keys and values never enter diagnostics. */
  label?: string;
  logger?: { debug(message: string): void };
}

let nextScopeId = 0;

/** A scoped async loading cache, similar to Guava's LoadingCache.
 * Shares results, failures and in-flight reads until close; consumers must not mutate results.
 * Unlike a long-lived LoadingCache, this scope has no refresh/expiry or automatic batching.
 * Keys and loading operations belong to adapters; the same key must have the same result type.
 * Operations must honor their signal for prompt cancellation. No underlying client is owned.
 */
export class DataLoader {
  readonly #scopeId = ++nextScopeId;
  readonly #readIds = new WeakMap<Promise<unknown>, number>();
  #nextReadId = 0;
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

  /** @rule Diagnostics never expose cache keys, loaded values or exception messages, or alter reads. */
  #debug(event: string, readId?: number, reason?: string): void {
    if (!this.options.logger) return;
    try {
      this.options.logger.debug(`[data-loader] scope=${this.#scopeId}`
        + (this.options.label ? ` label=${this.options.label}` : "")
        + (readId === undefined ? "" : ` read=${readId}`)
        + ` event=${event}` + (reason ? ` reason=${reason}` : ""));
    } catch { /* A diagnostic sink cannot change cache or query behavior. */ }
  }

  /** Undefined keys bypass caching. A waiter's signal cancels only that wait, not shared work. */
  async read<T>(key: unknown, operation: (signal: AbortSignal) => Promise<T>, signal?: AbortSignal): Promise<T> {
    this.#controller.signal.throwIfAborted();
    signal?.throwIfAborted();
    let pending = key === undefined ? undefined : this.#entries.get(key);
    if (pending) {
      this.#debug(this.#pending.has(pending) ? "wait" : "hit", this.#readIds.get(pending));
    } else {
      const readId = ++this.#nextReadId;
      const retain = key !== undefined && this.#entries.size < this.options.maxEntries;
      this.#debug(retain ? "miss" : "bypass", readId,
        retain ? undefined : key === undefined ? "no-key" : "entry-limit");
      pending = Promise.resolve().then(async () => {
        this.#controller.signal.throwIfAborted();
        const value = await operation(this.#controller.signal);
        this.#controller.signal.throwIfAborted();
        if (retain) {
          let bytes: number;
          let reason = "byte-limit";
          try { bytes = serialize(value).byteLength; } catch { bytes = Infinity; reason = "not-serializable"; }
          if (this.#bytes + bytes <= this.options.maxBytes) this.#bytes += bytes;
          else {
            this.#entries.delete(key);
            this.#debug("not-retained", readId, reason);
          }
        }
        return value;
      });
      this.#readIds.set(pending, readId);
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
      this.#debug("close");
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
