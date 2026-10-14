/**
 * An object referenced by a string identifier; the model owns its format and uniqueness scope.
 * @spec The same business identity yields the same identifier; descriptive metadata is excluded.
 */
export interface Identity {
  id(): string;
}
