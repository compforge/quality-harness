"""Display-tree projections for alternate trace perspectives."""

from __future__ import annotations

from dataclasses import dataclass

from trace_harness.model.node import Node
from trace_harness.view.display import DisplayNode
from trace_harness.view.facet import TracePerspective
from trace_harness.view.registry import FacetRegistry


@dataclass
class _ProjectedNode:
    display: DisplayNode
    hidden: int


def _source_count(display: DisplayNode) -> int:
    return max(len(display.node_ids), 1)


def _connector(hidden: int, node_ids: list[str], children: list[DisplayNode]) -> DisplayNode:
    return DisplayNode(
        kind="",
        name=f"… +{hidden} 个上下文节点",
        node_ids=list(dict.fromkeys(node_ids)),
        children=children,
    )


def project_perspective(
    roots: list[DisplayNode],
    by_id: dict[str, Node],
    registry: FacetRegistry,
    perspective: TracePerspective,
) -> list[DisplayNode]:
    """投影显示树；底层 Node.parent_node_id 始终只读，不生成第二套分析拓扑。"""
    if perspective == "full":
        return roots

    def visit(display: DisplayNode) -> _ProjectedNode | None:
        projected_children = [
            projected for child in display.children if (projected := visit(child))
        ]
        node = by_id.get(display.node_ids[0]) if display.kind and display.node_ids else None
        level = registry.perspective_level(node, perspective) if node else "detail"
        if level == "primary" or (level == "context" and projected_children):
            return _ProjectedNode(
                DisplayNode(
                    kind=display.kind,
                    name=display.name,
                    brief=display.brief,
                    node_ids=display.node_ids,
                    children=[projected.display for projected in projected_children],
                    findings=display.findings,
                    folded=0,
                ),
                hidden=0,
            )
        if not projected_children:
            return None

        own_hidden = _source_count(display)
        if len(projected_children) == 1 and projected_children[0].hidden > 0:
            child = projected_children[0]
            hidden = own_hidden + child.hidden
            return _ProjectedNode(
                _connector(
                    hidden,
                    [*display.node_ids, *child.display.node_ids],
                    child.display.children,
                ),
                hidden=hidden,
            )
        return _ProjectedNode(
            _connector(
                own_hidden,
                display.node_ids,
                [projected.display for projected in projected_children],
            ),
            hidden=own_hidden,
        )

    return [projected.display for root in roots if (projected := visit(root))]
