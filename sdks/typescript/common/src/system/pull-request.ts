import type { Identity } from "../identity.js";
import { repositoryId, type Repository } from "./repository.js";

export type PullRequestState = "open" | "merged" | "closed";

/**
 * A code-review proposal owned by its target Repository (GitHub PR or GitLab MR).
 * @spec Identity is target Repository + repository-local number; source, revisions and state are metadata.
 * @spec Omitted facts are unknown. A missing state or mergedAt never proves closure or merge.
 * @see {@link component://docs/pull-request.md}
 */
export interface PullRequest extends Identity {
  readonly repository: Repository;
  /** Positive GitHub PR number or GitLab MR iid, not the provider's global database ID. */
  readonly number: number;
  readonly title?: string;
  readonly url?: string;
  readonly state?: PullRequestState;
  readonly sourceRepository?: Repository;
  readonly sourceBranch?: string;
  readonly targetBranch?: string;
  readonly headSha?: string;
  readonly baseSha?: string;
  /** Provider timestamps, represented as ISO 8601 strings. */
  readonly updatedAt?: string;
  readonly mergedAt?: string;
}

/** Derive identity from fields; never trust a consumer's display URL or repository id method. */
export function pullRequestId(pr: Pick<PullRequest, "repository" | "number">): string {
  return `${repositoryId(pr.repository)}#${pr.number}`;
}
