"""Stable identity of one buildable component within a code repository."""

from __future__ import annotations

from dataclasses import dataclass, field

from harness_common.system.repository import Repository
from harness_common.system.tool import Tool
from harness_common.system.depot import Depot


@dataclass(frozen=True, slots=True)
class Component:
    """A named component owned by a repository; one repository may own many.

    @spec Description, language, tools and depot are optional metadata, not identity.
    Ecosystem is derived from language; consumers own tool discovery and execution.
    """

    repository: Repository
    name: str
    description: str | None = field(default=None, kw_only=True, compare=False)
    language: str | None = field(default=None, kw_only=True, compare=False)
    # None means undeclared; () declares no tools.
    tools: tuple[Tool, ...] | None = field(default=None, kw_only=True, compare=False)
    depot: Depot | None = field(default=None, kw_only=True, compare=False)

    @property
    def ecosystem(self) -> str | None:
        """Tool ecosystem, or None when language has no known unambiguous mapping."""
        if self.language in ("python", "go"):
            return self.language
        if self.language in ("javascript", "typescript"):
            return "node"
        return None
