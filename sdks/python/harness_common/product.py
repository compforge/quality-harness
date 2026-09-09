"""Stable identity of one business product."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class Product:
    """A business product that may use multiple Components."""

    name: str
