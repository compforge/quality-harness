"""Project HTTP sequence findings into Group operations without reinterpreting raw spans."""

from trace_harness.kinds.base import _fmt_ms
from trace_harness.model.node import Field, Finding
from trace_harness.model.viewtree import ViewTree
from trace_harness.view.facet import ChildOp, Expand, Fold, Group


def http_call_groups(view: ViewTree, findings: dict[str, list[Finding]]) -> dict[str, Group]:
    groups = {}
    for items in findings.values():
        for finding in items:
            if finding.source != "http_serial_same_api":
                continue
            data = finding.data
            span_ids = data.get("span_ids", [])
            members = [view.by_span[sid] for sid in span_ids if sid in view.by_span]
            # Older/custom IR can fuse requests or omit them. Do not manufacture members,
            # move nodes across parents, or absorb an unrelated sibling into the sequence.
            if len(members) < 2 or len({n.node_id for n in members}) != len(span_ids):
                continue
            parent_id = members[0].parent_node_id
            if any(n.parent_node_id != parent_id for n in members):
                continue
            parent = view.by_id.get(parent_id)
            siblings = view.children(parent) if parent else view.roots
            start = siblings.index(members[0])
            if siblings[start : start + len(members)] != members:
                continue
            groups[members[0].node_id] = Group(
                members,
                label=f"⚠ {data['method']} {data['target']}{data['route']} ×{data['count']}",
                brief=[
                    Field("wall-clock", _fmt_ms(data["wall_ms"]), "strong"),
                    Field("HTTP", _fmt_ms(data["http_total_ms"])),
                    Field("gap", _fmt_ms(data["gap_ms"]), "strong"),
                ],
            )
    return groups


def group_http_calls(ops: list[ChildOp], groups: dict[str, Group]) -> list[ChildOp]:
    """Replace only ordinary expand/fold operations; explicit facet layouts retain control."""
    output = []
    i = 0
    while i < len(ops):
        op = ops[i]
        group = groups.get(op.node.node_id) if isinstance(op, Expand | Fold) else None
        if group is not None:
            segment = ops[i : i + len(group.nodes)]
            if len(segment) == len(group.nodes) and all(
                isinstance(item, Expand | Fold) and item.node.node_id == member.node_id
                for item, member in zip(segment, group.nodes, strict=True)
            ):
                output.append(group)
                i += len(group.nodes)
                continue
        output.append(op)
        i += 1
    return output


def group_http_services(ops: list[ChildOp]) -> list[ChildOp]:
    """Collect adjacent HTTP rows under their caller service, retaining nested API groups."""
    output: list[ChildOp] = []
    pending: list[ChildOp] = []
    members = []

    def flush() -> None:
        if len(members) >= 2:
            output.append(
                Group(
                    list(members),
                    label=members[0].service or "?",
                    collapsed=False,
                    children=list(pending),
                )
            )
        else:
            output.extend(pending)
        pending.clear()
        members.clear()

    for op in ops:
        nodes = (
            [op.node]
            if isinstance(op, Expand | Fold)
            else op.nodes
            if isinstance(op, Group) and op.children is None
            else []
        )
        eligible = nodes and all(n.kind == "http" and n.service == nodes[0].service for n in nodes)
        if not eligible:
            flush()
            output.append(op)
            continue
        if members and members[0].service != nodes[0].service:
            flush()
        pending.append(op)
        members.extend(nodes)
    flush()
    return output
