import type { Repository } from "./repository.js";

/** A buildable component within a repository; one repository may own many. */
export interface Component {
  readonly repository: Repository;
  readonly name: string;
  /** Human-readable discovery metadata; not part of Component identity. */
  readonly description?: string;
}
