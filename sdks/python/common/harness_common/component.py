"""Stable identity of one buildable component within a code repository."""

from __future__ import annotations

from dataclasses import dataclass, field

from harness_common.repository import Repository


@dataclass(frozen=True, slots=True)
class Component:
    """A named component owned by a repository; one repository may own many.

    Description and language are optional metadata, not part of identity.
    Ecosystem is derived from language; consumers own tool discovery and execution.
    """

    repository: Repository
    name: str
    description: str | None = field(default=None, kw_only=True, compare=False)
    language: str | None = field(default=None, kw_only=True, compare=False)

    @property
    def ecosystem(self) -> str | None:
        """Tool ecosystem, or None when language has no known unambiguous mapping."""
        if self.language in ("python", "go"):
            return self.language
        if self.language in ("javascript", "typescript"):
            return "node"
        return None
