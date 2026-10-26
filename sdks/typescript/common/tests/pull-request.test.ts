import { expect, test } from "bun:test";
import { readFileSync } from "node:fs";
import { pullRequestId, repositoryId, type PullRequest, type PullRequestState, type Repository, type Identity } from "../src/index.js";

interface WireRepository { forge: { name: string }; path: string; forked_from?: string }
interface WirePullRequest {
  repository: WireRepository; number: number; title?: string; url?: string; state?: PullRequestState;
  source_repository?: WireRepository; source_branch?: string; target_branch?: string;
  head_sha?: string; base_sha?: string; updated_at?: string; merged_at?: string;
}
const repository = (wire: WireRepository): Repository => ({ forge: wire.forge, path: wire.path,
  ...(wire.forked_from ? { forkedFrom: wire.forked_from } : {}), id() { return repositoryId(this); } });
const rows = JSON.parse(readFileSync(new URL("../../../../conformance/common/pull-requests.json", import.meta.url), "utf8")) as { pull_request: WirePullRequest; id: string }[];
const prs: PullRequest[] = rows.map(({ pull_request: p }) => ({
  repository: repository(p.repository), number: p.number, title: p.title, url: p.url, state: p.state,
  sourceRepository: p.source_repository ? repository(p.source_repository) : undefined,
  sourceBranch: p.source_branch, targetBranch: p.target_branch, headSha: p.head_sha, baseSha: p.base_sha,
  updatedAt: p.updated_at, mergedAt: p.merged_at, id() { return pullRequestId(this); },
}));

// @case Fork origin, state, commit and timestamp changes retain the target repository identity.
test("PullRequest IDs follow the shared fixture and exclude observation metadata", () => {
  prs.forEach((pr, index) => { const identity: Identity = pr; expect(identity.id()).toBe(rows[index].id); });
  expect(new Set(prs.slice(0, 4).map(pullRequestId)).size).toBe(1);
  expect(new Set(prs.map(pullRequestId)).size).toBe(4);
  expect(prs[0].sourceRepository!.id()).not.toBe(prs[0].repository.id());
  expect(pullRequestId({ ...prs[0], repository: { ...prs[0].repository, id: () => "display-only" } })).toBe(rows[0].id);
});

test("optional PR facts roundtrip without inventing state, source or merge time", () => {
  const wireRepository = (r: Repository): WireRepository => ({ forge: r.forge, path: r.path, forked_from: r.forkedFrom ?? undefined });
  prs.forEach(pr => {
    const wire: WirePullRequest = { repository: wireRepository(pr.repository), number: pr.number, title: pr.title, url: pr.url,
      state: pr.state, source_repository: pr.sourceRepository ? wireRepository(pr.sourceRepository) : undefined,
      source_branch: pr.sourceBranch, target_branch: pr.targetBranch, head_sha: pr.headSha, base_sha: pr.baseSha,
      updated_at: pr.updatedAt, merged_at: pr.mergedAt };
    expect(JSON.parse(JSON.stringify(wire))).toEqual(rows[prs.indexOf(pr)].pull_request);
  });
  expect(prs[3].state).toBeUndefined(); expect(prs[3].sourceRepository).toBeUndefined();
  expect(prs[2].mergedAt).toBeUndefined(); expect(prs[1].state).toBe("merged");
});
