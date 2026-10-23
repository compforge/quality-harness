"""Stable identity of a source-code forge."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class Forge:
    """A system that hosts source-code repositories."""

    name: str
