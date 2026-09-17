import type { ClientManager } from "./client-manager.js";
import type { Environment } from "./environment.js";

/**
 * Execution-scoped environment access, independent of fixture orchestration.
 * @rule Borrows clients; does not initialize or dispose resources.
 * Deadline uses performance.now() milliseconds, not wall-clock time.
 * Consumers propagate remainingMs; the context itself does not cancel work.
 * Consumers extend the shared Environment identity with their own platform access.
 */
export class EnvironmentContext<E extends Environment = Environment> {
  constructor(
    readonly environment: E,
    readonly clients: Pick<ClientManager, "get">,
    readonly deadlineMs: number,
  ) {}

  get remainingMs(): number {
    return Math.max(0, this.deadlineMs - performance.now());
  }
}
