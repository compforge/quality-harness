import type { Forge } from "./forge.js";

/**
 * A repository identified by its Forge and path within that Forge.
 * @spec forkedFrom is the direct parent path within the same Forge instance, not repository identity.
 * @see {@link component://docs/kernel.md}
 */
export interface Repository {
  readonly forge: Forge;
  readonly path: string;
  /**
   * Direct parent repository path within this same Forge instance; omission/null is unspecified.
   * Cross-Forge import/mirror provenance belongs in separate metadata.
   */
  readonly forkedFrom?: string | null;
}
