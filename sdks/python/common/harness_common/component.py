"""Stable identity of one buildable component within a code repository."""

from __future__ import annotations

from dataclasses import dataclass, field

from harness_common.repository import Repository


@dataclass(frozen=True, slots=True)
class Component:
    """A named component owned by a repository; one repository may own many.

    The description is human-readable discovery metadata, not part of identity.
    """

    repository: Repository
    name: str
    description: str | None = field(default=None, kw_only=True, compare=False)
