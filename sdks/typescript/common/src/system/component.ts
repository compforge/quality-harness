import type { Depot } from "./depot.js";
import type { Tool } from "./tool.js";
import type { Repository } from "./repository.js";

/** A buildable component within a repository; one repository may own many.
 * @spec language, tools and depot are optional declarations, not Component identity or runtime evidence.
 */
export interface Component {
  readonly repository: Repository;
  readonly name: string;
  /** Human-readable discovery metadata; not part of Component identity. */
  readonly description?: string;
  /** Optional implementation language, supplied by consumers; not part of identity. */
  readonly language?: string;
  /** Omitted means undeclared; an empty list declares no tools. */
  readonly tools?: readonly Tool[];
  /** Logical content declaration, resolved separately in each checkout. */
  readonly depot?: Depot;
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
