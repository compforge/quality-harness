"""Stable identity of one business product."""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True, slots=True)
class Product:
    """A business product that may use multiple Components.

    Description is an optional short introduction. Concepts and workflows are
    optional Markdown describing the product's domain objects, relationships,
    constraints and cross-service flows. All three are discovery metadata, not
    identity, executable plans or evidence of a running product.
    """

    name: str
    description: str | None = field(default=None, kw_only=True, compare=False)
    concepts: str | None = field(default=None, kw_only=True, compare=False)
    workflows: str | None = field(default=None, kw_only=True, compare=False)
