from __future__ import annotations

import json

from trace_harness.model.context import TraceContext
from trace_harness.model.measurement import Measurements
from trace_harness.model.node import Node
from trace_harness.view.display import Compact, DisplayName, DisplayNode, name_projections
from trace_harness.view.measurements import measurement_rows
from trace_harness.view.tool_name import tool_name_detail

ATTR_TRUNCATE = 4000  # 单 attr 值超过即截断；完整原文按 span_id 走 dump-io


def _disp_payload(
    ctx: TraceContext,
    d: DisplayNode,
    byid: dict[str, Node],
    measurements: Measurements,
    path: str,
) -> dict:
    """DisplayNode（facet 折叠后的显示树）→ 交互页 payload。timing/原文/facts 用 `node_ids`
    回查底层 Node（duration 优先 wall_ms）；折叠/聚合合成行没有独立的底层 node，
    时间用所聚合 node 的包络。findings 由 engine 绑在 DisplayNode 上（折叠则已上浮）。"""
    brief = "  ".join(f"{f.label}={f.value}" for f in d.brief)
    fnd = [{"severity": f.severity, "source": f.source, "note": f.note} for f in d.findings]
    kids = [
        _disp_payload(ctx, c, byid, measurements, f"{path}.{i}") for i, c in enumerate(d.children)
    ]
    node = byid.get(d.node_ids[0]) if (d.kind and d.node_ids) else None
    if node is not None:
        display_name = str(node.facts.get("tool") or d.name) if node.kind == "tool-call" else d.name
        arguments = (
            ctx.raw_attr(str(node.facts.get("io_span") or node.primary_span_id)).get(
                "gen_ai.tool.call.arguments"
            )
            if node.kind == "tool-call"
            else None
        )
        compact = (
            d
            if isinstance(d, Compact)
            else (
                DisplayName(display_name, tool_name_detail(arguments))
                if node.kind == "tool-call"
                else d
            )
        )
        return {
            "node_id": node.node_id,
            "source_node_ids": [node.node_id],
            "kind": d.kind,
            "name": d.name,
            "name_variants": name_projections(compact, d.name),
            "service": node.service,
            "start_ms": node.start_ms,
            "duration_ms": node.facts.get("wall_ms", node.duration_ms),
            "has_error": node.has_error,
            "error": node.error_text if node.has_error else "",
            "brief": brief,
            "findings": fnd,
            "facts": {k: v for k, v in node.facts.items() if not k.startswith("_")},
            "measurements": measurement_rows(measurements, node.node_id),
            "span_ids": node.span_ids,
            "primary_span_id": node.primary_span_id,
            "error_span_ids": node.error_span_ids,
            "folded": d.folded,  # >0 = 折叠合成节点，前端默认收起（点开才展）
            "children": kids,
        }
    # 合成行（折叠/聚合摘要，kind 空、无单一底层 node）：时间取所聚合 node 的包络
    folded = [byid[nid] for nid in d.node_ids if nid in byid]
    start = min((n.start_ms for n in folded), default=0.0)
    end = max((n.end_ms for n in folded), default=0.0)
    return {
        # Nested service/API groups can reference identical members; the display path is unique.
        "node_id": f"fold:{path}",
        "source_node_ids": list(d.node_ids),
        "kind": "",
        "name": d.name,
        "name_variants": name_projections(d),
        "service": "",
        "start_ms": start,
        "duration_ms": end - start,
        "has_error": False,
        "error": "",
        "brief": brief,
        "findings": fnd,
        "facts": {},
        "details": {},
        "span_ids": [],
        "primary_span_id": "",
        "error_span_ids": [],
        "folded": d.folded,  # >0 = 折叠合成节点，前端默认收起
        "children": kids,
    }


def _span_payload(ctx: TraceContext, sid: str) -> dict:
    s = ctx.spans[sid]
    attrs = {}
    for k, v in (s.attrs or {}).items():
        text = v if isinstance(v, str) else json.dumps(v, ensure_ascii=False)
        if len(text) > ATTR_TRUNCATE:
            text = (
                text[:ATTR_TRUNCATE]
                + f"\n…[已截断，共 {len(text)} 字符；完整原文：dump-io --span {sid}]"
            )
        attrs[k] = text
    return {
        "service": s.service,
        "operation": s.name,
        "duration_ms": s.dur_ms,
        "has_error": s.has_error,
        "error": ctx.error_text(sid) if s.has_error else "",
        "attrs": attrs,
    }
