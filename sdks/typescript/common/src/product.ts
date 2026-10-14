import type { Knowledge } from "./knowledge.js";

/** A business identity independent of repository layout.
 * Consumers own its many-to-many associations with Components.
 * @spec Description and Knowledge describe the product, independently of identity and runtime evidence.
 * @see {@link component://docs/kernel.md}
 */
export interface Product {
  readonly name: string;
  /** Structured offline knowledge; not part of identity or runtime evidence. */
  readonly knowledge?: Knowledge;
  /** Optional short introduction; not part of Product identity. */
  readonly description?: string;
}
