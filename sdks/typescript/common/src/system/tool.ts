/** A development tool declared for a Component, such as pnpm, uv or Vite.
 * @spec A declaration does not prove installation or select an executable.
 */
export interface Tool {
  readonly name: string;
  /** Declared version or constraint, not an observed installed version. */
  readonly version?: string;
}
