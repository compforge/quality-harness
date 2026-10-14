"""Stable identity of one source-code repository."""

from __future__ import annotations

from dataclasses import dataclass, field

from harness_common.forge import Forge


@dataclass(frozen=True, slots=True)
class Repository:
    """A repository identified by its Forge and path within that Forge."""

    forge: Forge
    path: str
    # Direct parent repository path within this same Forge instance.
    # Optional provenance, not identity; cross-Forge imports/mirrors use separate metadata.
    forked_from: str | None = field(default=None, kw_only=True, compare=False)
