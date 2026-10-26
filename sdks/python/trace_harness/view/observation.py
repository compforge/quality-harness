"""Observation rendering with Trace-specific evidence drill-down."""

from __future__ import annotations

import json
from datetime import datetime
from typing import Any

from agent_ledger.observation import Input, ModelCall, Operation, SessionView, ToolCall, Turn
from agent_ledger.observation import Node as ObservationNode

from trace_harness.model.context import TraceContext
from trace_harness.model.node import Finding
from trace_harness.observation import source_node_ids, validate_session_views
from trace_harness.view.display import DisplayName, name_projections
from trace_harness.view.tool_name import tool_name_detail


def _text(value: Any) -> str:
    if isinstance(value, str):
        return value
    return json.dumps(value, ensure_ascii=False, indent=2)


def _source_payload(
    context: TraceContext,
    source_node_ids: tuple[str, ...],
    findings: dict[str, list[Finding]],
    status: str = "",
) -> dict[str, Any]:
    by_id = context.tree().by_id
    nodes = [by_id[node_id] for node_id in source_node_ids if node_id in by_id]
    span_ids = list(dict.fromkeys(span_id for node in nodes for span_id in node.span_ids))
    error_span_ids = list(
        dict.fromkeys(span_id for node in nodes for span_id in node.error_span_ids)
    )
    item_findings = [
        {
            "severity": finding.severity,
            "source": finding.source,
            "note": finding.note,
        }
        for node in nodes
        for finding in findings.get(node.node_id, [])
    ]
    status_error = status.lower() in {"error", "failed", "failure"}
    return {
        "service": nodes[0].service if nodes else "",
        "has_error": bool(error_span_ids) or status_error,
        "error": (
            context.error_text(error_span_ids[0])
            or next((n.error_text for n in nodes if n.error_text), status)
        )
        if error_span_ids
        else status,
        "findings": item_findings,
        "span_ids": span_ids,
        "source_node_ids": list(source_node_ids),
        "primary_span_id": nodes[0].primary_span_id if nodes else "",
        "error_span_ids": error_span_ids,
    }


def _facts(
    status: str,
    attributes: dict[str, Any],
    source_node_ids: tuple[str, ...],
) -> dict[str, str]:
    values = {key: _text(value) for key, value in attributes.items()}
    if status:
        values = {"status": status, **values}
    if source_node_ids:
        values["source_nodes"] = ", ".join(source_node_ids)
    return values


def _payload_has_error(payload: dict[str, Any]) -> bool:
    return bool(payload["has_error"]) or any(
        _payload_has_error(child) for child in payload["children"]
    )


def _sources(item: ObservationNode, context: TraceContext) -> tuple[str, ...]:
    own = source_node_ids(item, context)
    if own or not isinstance(item, (Turn, Operation)):
        return own
    return tuple(dict.fromkeys(ref for child in item.nodes for ref in _sources(child, context)))


def _timing(item: ObservationNode) -> dict[str, Any]:
    start = (
        datetime.fromisoformat(item.started_at.replace("Z", "+00:00")).timestamp() * 1000
        if item.started_at
        else None
    )
    end = (
        datetime.fromisoformat(item.finished_at.replace("Z", "+00:00")).timestamp() * 1000
        if item.finished_at
        else None
    )
    # Numeric zero is only the trace viewer's layout fallback; facts retain unavailable timing.
    return {
        "timing_known": start is not None and end is not None,
        "start_ms": start or 0,
        "duration_ms": end - start if start is not None and end is not None else 0,
    }


def _item_payload(
    context: TraceContext,
    item: ObservationNode,
    identity: list[str],
    findings: dict[str, list[Finding]],
    catalog: dict[tuple[str, str], SessionView],
    active: frozenset[tuple[str, str]],
) -> dict[str, Any]:
    sources = _sources(item, context)
    facts = _facts(item.status, item.attributes, sources)
    if item.started_at:
        facts["started_at"] = item.started_at
    if item.finished_at:
        facts["finished_at"] = item.finished_at
    facts["evidence"] = _text([ref.model_dump() for ref in item.evidence])
    if item.run_id:
        facts["run_id"] = item.run_id
    if item.lane_id:
        facts["lane_id"] = item.lane_id
    details = {}
    detail = ""
    brief = ""
    if isinstance(item, (ModelCall, ToolCall)):
        if item.request is not None:
            details["request"] = _text(item.request.model_dump(mode="json"))
        if item.response is not None:
            details["response"] = _text(item.response.model_dump(mode="json"))
        if isinstance(item, ToolCall):
            detail = tool_name_detail(item.request.input) if item.request else ""
            if item.tool_call_id:
                facts["tool_call_id"] = item.tool_call_id
            if item.model_call_id:
                facts["model_call_id"] = item.model_call_id
        elif item.request and item.request.model:
            facts["requested_model"] = _text(item.request.model.model_dump())
            brief = f"model={item.request.model.id}"
    elif isinstance(item, Input):
        details["content"] = _text(item.content)
        if item.mode:
            facts["mode"] = item.mode
    elif isinstance(item, Operation):
        facts["operation"] = item.operation
        details["data"] = _text(item.data)
    name = (
        item.name
        or (item.request.tool_name if isinstance(item, ToolCall) and item.request else None)
        or (item.operation if isinstance(item, Operation) else item.kind)
    )
    children = (
        [_item_payload(context, child, identity, findings, catalog, active) for child in item.nodes]
        if isinstance(item, (Turn, Operation))
        else []
    )
    if isinstance(item, ToolCall):
        links = []
        seen: set[tuple[str, str]] = set()
        for ref in item.sessions:
            key = (ref.source, ref.id)
            if key in seen:
                continue
            seen.add(key)
            state = "cycle" if key in active else "available" if key in catalog else "unavailable"
            links.append({**ref.model_dump(), "state": state})
            if state == "available":
                children.append(
                    _session_payload(
                        context, catalog[key], findings, catalog, active, [*identity, item.id]
                    )
                )
        if links:
            details["sessions"] = _text(links)
    source = _source_payload(context, sources, findings, item.status)
    return {
        "node_id": "observation:"
        + json.dumps([*identity, item.id], ensure_ascii=False, separators=(",", ":")),
        "kind": item.kind,
        "name": name,
        "name_variants": name_projections(DisplayName(name, detail)),
        **_timing(item),
        "brief": brief,
        "facts": facts,
        "details": details,
        "folded": 0,
        "collapsed": isinstance(item, Operation)
        and bool(children)
        and not source["has_error"]
        and not any(_payload_has_error(child) for child in children),
        "children": children,
        **source,
    }


def _session_payload(
    context: TraceContext,
    view: SessionView,
    findings: dict[str, list[Finding]],
    catalog: dict[tuple[str, str], SessionView],
    active: frozenset[tuple[str, str]],
    path: list[str],
) -> dict[str, Any]:
    key = (view.session.source, view.session.id)
    identity = [*path, *key]
    children = [
        _item_payload(context, item, identity, findings, catalog, active | {key})
        for item in view.nodes
    ]
    sources = tuple(dict.fromkeys(ref for item in view.nodes for ref in _sources(item, context)))
    return {
        "node_id": "session:" + json.dumps(identity, ensure_ascii=False, separators=(",", ":")),
        "kind": "session",
        "name": view.session.id,
        "name_variants": [view.session.id],
        "timing_known": False,
        "start_ms": 0,
        "duration_ms": 0,
        "brief": view.session.source,
        "facts": _facts("", view.attributes, sources),
        "details": {
            "issues": _text(view.issues),
            "lanes": _text([lane.model_dump() for lane in view.lanes]),
        },
        "folded": 0,
        "children": children,
        **_source_payload(context, sources, findings),
    }


def session_roots(
    context: TraceContext, views: tuple[SessionView, ...], findings: dict[str, list[Finding]]
) -> list[dict[str, Any]]:
    """Expand supplied Session references at call sites; cycles and absent views stay references."""
    views = validate_session_views(views, context)
    catalog = {(view.session.source, view.session.id): view for view in views}
    return [_session_payload(context, view, findings, catalog, frozenset(), []) for view in views]
