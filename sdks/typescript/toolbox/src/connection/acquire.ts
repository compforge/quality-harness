import type { ClientLifecycle } from "../datasource";
import { ToolboxError } from "../errors";

/** Stop waiting without leaving rejected preparation promises unobserved. */
export async function awaitWithSignal<T>(pending: Promise<T>, signal: AbortSignal, cancel?: () => void): Promise<T> {
  if (signal.aborted) {
    cancel?.();
    void pending.catch(() => {});
    signal.throwIfAborted();
  }
  let abort!: () => void;
  const cancelled = new Promise<never>((_, reject) => {
    abort = () => { cancel?.(); reject(signal.reason); };
    signal.addEventListener("abort", abort, { once: true });
  });
  try { return await Promise.race([pending, cancelled]); }
  finally { signal.removeEventListener("abort", abort); }
}

export interface ConnectionAttempt {
  readonly signal: AbortSignal;
  /** Remaining mapping + handshake budget, not a fresh timeout for each phase. */
  remainingMs(): number;
}

interface Acquisition<R, C> {
  name: string;
  connectTimeoutMs: number;
  acquireTimeoutMs?: number;
  signal?: AbortSignal;
  open(route: R, attempt: ConnectionAttempt): Promise<{ connection: C; transport: string }>;
  close(connection: C): void | Promise<void>;
  /** Undefined means terminal; authentication and protocol errors must not change route. */
  retryReason(error: unknown): string | undefined;
  onRoute?: ClientLifecycle["onRoute"];
}

/**
 * @spec One acquisition owns ordered attempts and a deadline covering mapping and handshake.
 * @spec Cancellation keeps its original reason; a late successful connection is closed, never published.
 * @rule Only connection preparation enters this function. Commands and queries must never be replayed.
 * @why Protocol adapters own partial handles and error classification; all protocols share the same lifecycle.
 * @see {@link component://docs/toolbox.md}
 * @case id=connection_mapping_cancel,desc=`Cancel while mapping a route`,expect=`Acquisition exits without a late handshake`
 */
export async function acquireConnection<R extends { readonly name: string }, C>(
  routes: readonly R[], options: Acquisition<R, C>,
): Promise<C> {
  const budget = options.acquireTimeoutMs ?? options.connectTimeoutMs * routes.length;
  if (!routes.length) throw new ToolboxError(`${options.name} requires a transport`, { kind: "invalid_argument" });
  if ([options.connectTimeoutMs, budget].some(value => !Number.isFinite(value) || value <= 0 || value > 2_147_483_647)) {
    throw new ToolboxError(`${options.name} timeouts must be positive milliseconds within timer range`, { kind: "invalid_argument" });
  }
  options.signal?.throwIfAborted();
  const timeout = new AbortController();
  const timedOut = (message: string) => new ToolboxError(message, { kind: "timeout", code: "ETIMEDOUT" });
  const deadline = performance.now() + budget;
  const timer = setTimeout(() => timeout.abort(timedOut(`${options.name} timed out after ${budget}ms`)), budget);
  const signal = options.signal ? AbortSignal.any([options.signal, timeout.signal]) : timeout.signal;
  let reason: string | undefined;
  try {
    for (const [index, route] of routes.entries()) {
      if (performance.now() >= deadline) timeout.abort(timedOut(`${options.name} timed out after ${budget}ms`));
      signal.throwIfAborted();
      const attempt = new AbortController();
      const end = Math.min(deadline, performance.now() + options.connectTimeoutMs);
      const expire = () => attempt.abort(timedOut(`${options.name} attempt timed out (${route.name})`));
      const attemptTimer = setTimeout(expire, Math.max(1, Math.ceil(end - performance.now())));
      const attemptSignal = AbortSignal.any([signal, attempt.signal]);
      const context: ConnectionAttempt = {
        signal: attemptSignal,
        remainingMs: () => {
          if (performance.now() >= end) expire();
          attemptSignal.throwIfAborted();
          return Math.max(1, Math.ceil(end - performance.now()));
        },
      };
      let candidate: { connection: C; transport: string } | undefined;
      let discarded = false;
      const discard = async () => {
        if (!candidate || discarded) return;
        discarded = true;
        await options.close(candidate.connection);
      };
      let result: { connection: C; transport: string };
      try {
        const prepared = options.open(route, context).then(async value => {
          candidate = value;
          if (attemptSignal.aborted) {
            await discard();
            attemptSignal.throwIfAborted();
          }
          return value;
        });
        result = await awaitWithSignal(prepared, attemptSignal);
      } catch (failure) {
        // Cancellation can win after open resolved but before its result was delivered.
        await discard();
        signal.throwIfAborted();
        const error = attemptSignal.aborted ? attemptSignal.reason : failure;
        reason = options.retryReason(error);
        if (reason === undefined || index === routes.length - 1) throw error;
        continue;
      } finally { clearTimeout(attemptTimer); }
      try {
        attemptSignal.throwIfAborted();
        options.onRoute?.({ transport: result.transport, reason });
        attemptSignal.throwIfAborted();
        return result.connection;
      } catch (error) { await discard(); throw error; }
    }
    throw new Error("Connection routes exhausted");
  } finally { clearTimeout(timer); }
}
