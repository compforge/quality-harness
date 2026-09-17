import type { Forge } from "./forge.js";

/** A repository identified by its Forge and path within that Forge. */
export interface Repository {
  readonly forge: Forge;
  readonly path: string;
}
