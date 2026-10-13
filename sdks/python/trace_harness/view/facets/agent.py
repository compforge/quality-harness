"""Agent perspective facets for generic GenAI agent and tool-call nodes."""

from __future__ import annotations

from trace_harness.model.node import Node
from trace_harness.view.facet import (
    DefaultFacet,
)


class AgentFacet(DefaultFacet):
    priority = 20

    def match(self, node: Node) -> bool:
        return node.kind == "agent"


class ToolCallFacet(DefaultFacet):
    priority = 20

    def match(self, node: Node) -> bool:
        return node.kind == "tool-call"
