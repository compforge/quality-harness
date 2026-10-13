/** A business identity independent of repository layout.
 * Consumers own its many-to-many associations with Components.
 * @spec Description, concepts and workflows describe product-wide knowledge, not identity or runtime evidence.
 * @see {@link component://docs/kernel.md}
 */
export interface Product {
  readonly name: string;
  /** Optional short introduction; not part of Product identity. */
  readonly description?: string;
  /** Optional Markdown: domain objects, relationships and constraints. */
  readonly concepts?: string;
  /** Optional Markdown: collaboration from trigger to completion; not an executable plan. */
  readonly workflows?: string;
}
