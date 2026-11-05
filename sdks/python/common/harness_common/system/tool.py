"""Development tool declarations, independent of discovery and execution."""

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class Tool:
    """A tool used by a Component, such as pnpm, uv or Vite.

    @spec A declaration does not prove installation or select an executable.
    """

    name: str
    # Declared version or constraint, not an observed installed version.
    version: str | None = None
