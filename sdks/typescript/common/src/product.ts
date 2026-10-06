/** A business identity independent of repository layout.
 * Consumers own its many-to-many associations with Components.
 */
export interface Product {
  readonly name: string;
}
