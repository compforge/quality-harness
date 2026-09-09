"""Agent perspective facets for generic GenAI agent and tool-call nodes."""

from __future__ import annotations

from trace_harness.model.node import Node
from trace_harness.view.facet import (
    DefaultFacet,
    PerspectiveLevel,
    TracePerspective,
)


class AgentFacet(DefaultFacet):
    priority = 20

    def match(self, node: Node) -> bool:
        return node.kind == "agent"

    def perspective_level(
        self, node: Node, perspective: TracePerspective
    ) -> PerspectiveLevel | None:
        return "primary" if perspective == "agent" else None


class ToolCallFacet(DefaultFacet):
    priority = 20

    def match(self, node: Node) -> bool:
        return node.kind == "tool-call"

    def perspective_level(
        self, node: Node, perspective: TracePerspective
    ) -> PerspectiveLevel | None:
        return "primary" if perspective == "agent" else None
