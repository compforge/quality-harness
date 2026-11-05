"""Logical declarations of a Component's recoverable supporting content."""

from dataclasses import dataclass
from typing import Literal


@dataclass(frozen=True, slots=True)
class Bin:
    """A recoverable content unit within a Depot, usually one directory.

    @spec path is a normalized repository-relative POSIX path, never checkout-absolute.
    @rule A declaration alone does not authorize deletion or prove exclusive ownership.
    @link component://docs/component-depot.md
    """

    path: str
    kind: Literal["dependencies", "output", "cache"]


@dataclass(frozen=True, slots=True)
class Depot:
    """The optional container for a Component's recoverable supporting content.

    @spec A Component has at most one Depot per checkout; missing contents do not remove its declaration.
    @spec Consumers own discovery, preparation, usage protection and reclamation.
    @link component://docs/component-depot.md
    """

    bins: tuple[Bin, ...]
