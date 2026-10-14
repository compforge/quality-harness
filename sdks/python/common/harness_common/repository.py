"""Stable identity of one source-code repository."""

from __future__ import annotations

from dataclasses import dataclass, field

from harness_common.forge import Forge
from harness_common.identity import Identity


@dataclass(frozen=True, slots=True)
class Repository(Identity):
    """A repository identified by its Forge and path within that Forge."""

    forge: Forge
    path: str
    # Direct parent repository path within this same Forge instance.
    # Optional provenance, not identity; cross-Forge imports/mirrors use separate metadata.
    forked_from: str | None = field(default=None, kw_only=True, compare=False)

    def id(self) -> str:
        """Return forge name/path, excluding fork provenance.

        @spec Repository identity is derived from Forge.name and path only.
        """
        return f"{self.forge.name}/{self.path}"
