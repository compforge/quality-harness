"""Views select and organize prepared data; they cannot acquire or analyze evidence."""

from typing import Literal, TypedDict

from trace_harness.analyze.call_breakdown import saved_call_breakdown
from trace_harness.analyze.context import AnalysisContext
from trace_harness.model.measurement import Measurements
from trace_harness.view.display import DisplayNode
from trace_harness.view.engine import render as render_display
from trace_harness.view.facets import builtin_facets
from trace_harness.view.measurements import MeasurementFilter, filter_measurements, measurement_rows
from trace_harness.view.observation import session_roots
from trace_harness.view.payload import _disp_payload, _span_payload
from trace_harness.view.registry import FacetRegistry


class ViewRequest(TypedDict, total=False):
    scope: Literal["trace", "node"]
    view: Literal["default", "agent", "measurements", "timeline"]
    nodeId: str
    offset: int
    limit: int


def page(items: list, request: ViewRequest) -> dict:
    offset, limit = request.get("offset", 0), request.get("limit", max(1, len(items)))
    if type(offset) is not int or type(limit) is not int or offset < 0 or limit < 1:
        raise ValueError("View offset must be non-negative and limit must be positive integers")
    return {
        "items": items[offset : offset + limit],
        "total": len(items),
        "offset": offset,
        "next_offset": offset + limit if offset + limit < len(items) else None,
    }


def _flatten(roots: list[dict]) -> list[dict]:
    rows = []

    def visit(nodes, depth=0, parent_id=None):
        for node in nodes:
            row = {k: v for k, v in node.items() if k != "children"}
            row["depth"] = depth
            if parent_id is not None:
                row["parent_id"] = parent_id
            rows.append(row)
            visit(node["children"], depth + 1, node["node_id"])

    visit(roots)
    return rows


def build_view(
    analysis: AnalysisContext,
    request: ViewRequest,
    *,
    facets: FacetRegistry | None = None,
    measurement_filter: MeasurementFilter | None = None,
) -> dict:
    trace, measurements = analysis.trace, analysis.measurements
    findings = {key: list(values) for key, values in analysis.findings.items()}
    kind = request.get("view", "default")
    by_id = trace.tree().by_id

    def spans(ids):
        return {sid: _span_payload(trace, sid) for sid in dict.fromkeys(ids) if sid in trace.spans}

    if request["scope"] == "trace":
        if kind not in {"default", "agent"}:
            raise ValueError(f"Invalid trace view: {kind}")
        visible = filter_measurements(trace, measurements, measurement_filter)
        if kind == "agent":
            roots = (
                session_roots(trace, analysis.session_views, findings)
                if analysis.session_views is not None
                else []
            )
        else:
            roots = [
                _disp_payload(trace, node, by_id, visible, str(i))
                for i, node in enumerate(
                    render_display(
                        trace.tree(), findings, registry=facets or FacetRegistry(builtin_facets())
                    )
                )
            ]
        rows = page(_flatten(roots), request)
        ids = {source for row in rows["items"] for source in row["source_node_ids"]}
        return {
            "scope": "trace",
            "view": kind,
            "trace_id": trace.trace_id,
            "status": "not_available"
            if kind == "agent" and analysis.session_views is None
            else "available",
            "rows": rows,
            "spans": spans(sid for row in rows["items"] for sid in row["span_ids"]),
            "values": {n.node_id: n.value for n in trace.nodes if n.node_id in ids and n.value},
            "timings": {
                nid: timing
                for nid in sorted(ids)
                if visible.nodes.get(nid) and (timing := saved_call_breakdown(measurements, nid))
            },
            "measurements": measurement_rows(measurements, None),
            "node_count": len(trace.nodes),
            "span_count": trace.span_count,
            "error_count": sum(n.has_error for n in trace.nodes),
        }
    if request["scope"] != "node":
        raise ValueError(f"Invalid view scope: {request['scope']}")
    node = by_id.get(request["nodeId"])
    if node is None:
        raise ValueError(f"Trace {trace.trace_id}: node {request['nodeId']} not found")
    if kind not in {"default", "measurements", "timeline"}:
        raise ValueError(f"Invalid node view: {kind}")
    identity = {
        "node_id": node.node_id,
        "source_node_ids": [node.node_id],
        "span_ids": list(node.span_ids),
        "depth": 0,
        "name": node.name,
        "kind": node.kind,
        "start_ms": node.start_ms,
        "duration_ms": node.duration_ms,
        "service": node.service,
    }
    base = {"scope": "node", "view": kind, "trace_id": trace.trace_id, "node": identity}
    if kind == "default":
        display = DisplayNode(
            kind=node.kind,
            name=node.name,
            brief=node.brief,
            node_ids=[node.node_id],
            findings=findings.get(node.node_id, []),
        )
        base["node"] = _flatten(
            [_disp_payload(trace, display, by_id, Measurements(), node.node_id)]
        )[0]
        return {**base, "spans": spans(node.span_ids)}
    prepared = saved_call_breakdown(measurements, node.node_id)
    base["timing"] = {k: prepared[k] for k in ("status", "error", "window")} if prepared else None
    if kind == "measurements":
        return {
            **base,
            "measurements": measurement_rows(measurements, node.node_id),
            "groups": page(
                sorted(prepared["groups"], key=lambda g: (-g["total"], g["key"])), request
            )
            if prepared and prepared["status"] == "measured"
            else None,
        }
    return {
        **base,
        "calls": page(sorted(prepared["calls"], key=lambda c: (c["start"], c["key"])), request)
        if prepared and prepared["status"] == "measured"
        else None,
    }
