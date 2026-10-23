import type { Repository } from "./repository.js";

/** A buildable component within a repository; one repository may own many. */
export interface Component {
  readonly repository: Repository;
  readonly name: string;
  /** Human-readable discovery metadata; not part of Component identity. */
  readonly description?: string;
  /** Optional implementation language, supplied by consumers; not part of identity. */
  readonly language?: string;
}

/** Derive the tool ecosystem from language; unknown mappings return undefined.
 * Package manager discovery and execution remain consumer responsibilities.
 */
export function componentEcosystem(component: Component): string | undefined {
  switch (component.language) {
    case "python":
    case "go":
      return component.language;
    case "javascript":
    case "typescript":
      return "node";
    default:
      return undefined;
  }
}
