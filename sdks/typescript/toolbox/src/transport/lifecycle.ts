import type { Client } from "../client";
import { acquireConnection, type Acquisition } from "../connection/acquire";
import type { TransportPath } from "./index";

/**
 * @spec A transport selects and prepares access paths; protocol establishment is supplied by the client.
 * @rule Borrowed routes are never disposed by their consumers. Register resource-owning routes with ClientManager.
 * @see {@link component://docs/toolbox.md}
 */
export class Transport implements Client {
  readonly #controller = new AbortController();
  /** Root cancellation stops borrowers; only this transport's disposal tears down shared resources. */
  protected readonly resourceSignal = this.#controller.signal;
  readonly signal: AbortSignal;
  readonly #pending = new Set<Promise<unknown>>();
  readonly #cleanupErrors: unknown[] = [];
  #disposal?: Promise<void>;

  constructor(private readonly candidates: readonly TransportPath[] = [], signal?: AbortSignal) {
    this.signal = signal ? AbortSignal.any([signal, this.#controller.signal]) : this.#controller.signal;
  }

  protected paths(): readonly TransportPath[] { return this.candidates; }
  async initialize(): Promise<void> { this.signal.throwIfAborted(); }

  /** Internal handshake collaboration: only establishment errors can select another path. */
  acquire<C>(options: Acquisition<TransportPath, C>): Promise<C> {
    this.signal.throwIfAborted();
    const signal = options.signal ? AbortSignal.any([options.signal, this.signal]) : this.signal;
    return acquireConnection(this.paths(), {
      ...options, signal,
      onPending: pending => {
        this.#pending.add(pending);
        void pending.then(() => this.#pending.delete(pending), () => this.#pending.delete(pending));
      },
      close: async connection => {
        try { await options.close(connection); }
        catch (error) { this.#cleanupErrors.push(error); throw error; }
      },
    });
  }

  protected async close(): Promise<void> {}

  dispose(): Promise<void> {
    return this.#disposal ??= (async () => {
      this.#controller.abort(new Error("Transport disposed"));
      const errors: unknown[] = [];
      try { await this.close(); } catch (error) { errors.push(error); }
      await Promise.allSettled([...this.#pending]);
      errors.push(...this.#cleanupErrors);
      if (errors.length) throw new AggregateError(errors, "Transport cleanup failed");
    })();
  }
}
