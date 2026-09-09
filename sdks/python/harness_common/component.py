"""Stable identity of one buildable component within a code repository."""

from __future__ import annotations

from dataclasses import dataclass

from harness_common.repository import Repository


@dataclass(frozen=True, slots=True)
class Component:
    """A named component owned by a repository; one repository may own many."""

    repository: Repository
    name: str
