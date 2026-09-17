import type { ClientProvider } from "./client-manager.js";

/**
 * Execution-scoped environment access, independent of fixture orchestration.
 * @rule Borrows clients; does not initialize or dispose resources.
 * Deadline uses performance.now() milliseconds, not wall-clock time.
 * Consumers propagate remainingMs; the context itself does not cancel work.
 * Environment's concrete type belongs to its consumer.
 */
export class EnvironmentContext<E> {
  constructor(
    readonly environment: E,
    readonly clients: ClientProvider,
    readonly deadlineMs: number,
  ) {}

  get remainingMs(): number {
    return Math.max(0, this.deadlineMs - performance.now());
  }
}
