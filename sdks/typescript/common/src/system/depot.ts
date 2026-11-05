/** A recoverable content unit within a Depot, usually one directory.
 * @spec path is a normalized repository-relative POSIX path, never checkout-absolute.
 * @rule A declaration alone does not authorize deletion or prove exclusive ownership.
 * @see {@link component://docs/component-depot.md}
 */
export interface Bin {
  readonly path: string;
  readonly kind: "dependencies" | "output" | "cache";
}

/** The optional logical container for a Component's recoverable supporting content.
 * @spec A Component has at most one Depot per checkout; missing contents do not remove its declaration.
 * @spec Consumers own discovery, preparation, usage protection and reclamation.
 * @see {@link component://docs/component-depot.md}
 */
export interface Depot {
  readonly bins: readonly Bin[];
}
