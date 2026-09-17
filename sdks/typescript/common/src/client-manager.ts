import type { Client } from "./client.js";
import type { ClientFactory } from "./client-factory.js";

/** Consumers borrow clients; only the root owner receives the disposal capability. */
export interface ClientProvider {
  get<C extends Client>(factory: ClientFactory<C>): Promise<C>;
}

/**
 * @spec One root execution shares successful initialization per identity and closes clients at finalize.
 * @rule Failed cleanup poisons the identity and remains a root disposal error; it never permits retry.
 */
export class ClientManager implements ClientProvider {
  readonly #controller = new AbortController();
  readonly signal: AbortSignal;
  readonly #clients = new Map<string, Promise<Client>>();
  readonly #ready: Client[] = [];
  readonly #unclean = new Set<string>();
  readonly #cleanupErrors: unknown[] = [];
  #disposal?: Promise<void>;

  constructor(signal?: AbortSignal) {
    this.signal = signal ? AbortSignal.any([signal, this.#controller.signal]) : this.#controller.signal;
  }

  get<C extends Client>(factory: ClientFactory<C>): Promise<C> {
    this.signal.throwIfAborted();
    const key = factory.key;
    const existing = this.#clients.get(key);
    if (existing) return existing as Promise<C>;
    // Publish before construction so concurrent callers never dispatch a second factory.
    const pending = Promise.resolve().then(async () => {
      this.signal.throwIfAborted();
      const client = factory.createClient(this, this.signal);
      try {
        await client.initialize();
        this.signal.throwIfAborted();
        this.#ready.push(client);
        return client;
      } catch (error) {
        try { await client.dispose(); }
        catch (cleanup) {
          this.#unclean.add(key);
          this.#cleanupErrors.push(cleanup);
          throw new AggregateError([error, cleanup], "Client initialization and cleanup failed");
        }
        throw error;
      }
    });
    this.#clients.set(key, pending);
    void pending.catch(() => {
      if (!this.#unclean.has(key) && this.#clients.get(key) === pending) this.#clients.delete(key);
    });
    return pending;
  }

  dispose(): Promise<void> {
    return this.#disposal ??= (async () => {
      this.#controller.abort(new Error("Client manager disposed"));
      await Promise.allSettled(this.#clients.values());
      this.#clients.clear();
      // Dependencies must be acquired before their consumers initialize, so reverse readiness is safe.
      const errors = [...this.#cleanupErrors];
      for (const client of this.#ready.splice(0).reverse()) {
        try { await client.dispose(); } catch (error) { errors.push(error); }
      }
      if (errors.length) throw new AggregateError(errors, "Client cleanup failed");
    })();
  }
}
