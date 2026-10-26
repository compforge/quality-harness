import type { Identity } from "../identity.js";
import { repositoryId, type Repository, type RepositoryKey, type RepositorySnapshot } from "./repository.js";

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

/** The target Repository and its local PR number / MR iid; no methods or observation facts are required. */
export interface PullRequestKey {
  readonly repository: RepositoryKey;
  readonly number: PullRequest["number"];
}

/**
 * @spec A PR snapshot retains the common field names and optional facts, without PR or nested Repository identity methods.
 * @why JSON and remote observations must not require each consumer to invent a parallel PR shape or reconstruct methods.
 * @see {@link component://docs/pull-request.md}
 */
export type PullRequestSnapshot = Omit<PullRequest, "id" | "repository" | "sourceRepository"> & {
  readonly repository: RepositorySnapshot;
  readonly sourceRepository?: RepositorySnapshot;
};

/** Derive identity from fields; never trust a consumer's display URL or repository id method. */
export function pullRequestId<T extends PullRequestKey>(pr: T): string {
  return `${repositoryId(pr.repository)}#${pr.number}`;
}
