"""Stable code-review proposal identity and optional platform facts."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal

from harness_common.identity import Identity
from harness_common.system.repository import Repository

PullRequestState = Literal["open", "merged", "closed"]


@dataclass(frozen=True, slots=True)
class PullRequest(Identity):
    """A GitHub PR or GitLab MR in its target Repository.

    @spec Identity is target Repository + local number, independent of source, revisions and state.
    @spec Missing facts are unknown; neither absent state nor merged_at proves a terminal state.
    @link component://docs/pull-request.md
    """

    repository: Repository
    # Positive repository-local PR number / MR iid, not a global database ID.
    number: int
    title: str | None = field(default=None, kw_only=True, compare=False)
    url: str | None = field(default=None, kw_only=True, compare=False)
    state: PullRequestState | None = field(default=None, kw_only=True, compare=False)
    source_repository: Repository | None = field(
        default=None, kw_only=True, compare=False
    )
    source_branch: str | None = field(default=None, kw_only=True, compare=False)
    target_branch: str | None = field(default=None, kw_only=True, compare=False)
    head_sha: str | None = field(default=None, kw_only=True, compare=False)
    base_sha: str | None = field(default=None, kw_only=True, compare=False)
    # ISO 8601 provider timestamps, not local collection times.
    updated_at: str | None = field(default=None, kw_only=True, compare=False)
    merged_at: str | None = field(default=None, kw_only=True, compare=False)

    def id(self) -> str:
        """Return target Repository ID followed by # and the local number."""
        return f"{self.repository.forge.name}/{self.repository.path}#{self.number}"
