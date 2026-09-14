"""Stable identity of one source-code repository."""

from __future__ import annotations

from dataclasses import dataclass

from harness_common.forge import Forge


@dataclass(frozen=True, slots=True)
class Repository:
    """A repository identified by its Forge and path within that Forge."""

    forge: Forge
    path: str
