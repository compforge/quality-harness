"""Generic AgentRun IR rendering payload for the interactive trace viewer."""

from __future__ import annotations

import json
from typing import Any

from trace_harness.model.agent import (
    AgentRun,
    AgentRunIR,
    AgentTurn,
    ModelCall,
    Operation,
    ToolCall,
    TurnItem,
)
from trace_harness.model.context import TraceContext
from trace_harness.model.node import Finding
from trace_harness.view.display import Compact, DisplayName, name_projections
from trace_harness.view.tool_name import tool_name_detail


def _text(value: Any) -> str:
    if isinstance(value, str):
        return value
    return json.dumps(value, ensure_ascii=False, indent=2)


def _compact_names(component: object, name: str, detail: str = "") -> list[str]:
    compact = component if isinstance(component, Compact) else DisplayName(name, detail)
    return name_projections(compact)


def _source_payload(
    context: TraceContext,
    source_node_ids: tuple[str, ...],
    findings: dict[str, list[Finding]],
    status: str = "",
) -> dict[str, Any]:
    by_id = context.view().by_id
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
        "error": context.error_text(error_span_ids[0]) if error_span_ids else status,
        "findings": item_findings,
        "span_ids": span_ids,
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


def _item_payload(
    context: TraceContext,
    item: TurnItem,
    findings: dict[str, list[Finding]],
) -> dict[str, Any]:
    facts = _facts(item.status, item.attributes, item.source_node_ids)
    brief_parts = []
    if isinstance(item, ModelCall) and item.model:
        facts = {"model": item.model, **facts}
        brief_parts.append(f"model={item.model}")
    elif isinstance(item, ToolCall):
        if item.tool_call_id:
            facts = {"tool_call_id": item.tool_call_id, **facts}
    agent_runs = item.agent_runs if isinstance(item, ToolCall | Operation) else ()
    operations = item.operations if isinstance(item, Operation) else ()
    if operations:
        brief_parts.append(f"{len(operations)} operation{'s' if len(operations) != 1 else ''}")
    if agent_runs:
        brief_parts.append(f"{len(agent_runs)} agent run{'s' if len(agent_runs) != 1 else ''}")
    features = {}
    if item.input is not None:
        features["input"] = _text(item.input)
    if item.output is not None:
        features["output"] = _text(item.output)
    children = [
        *(_item_payload(context, child, findings) for child in operations),
        *(_run_payload(context, run, findings) for run in agent_runs),
    ]
    children.sort(key=lambda child: (child["start_ms"], child["node_id"]))
    source = _source_payload(context, item.source_node_ids, findings, item.status)
    detail = tool_name_detail(item.input) if isinstance(item, ToolCall) else ""
    return {
        "node_id": f"agent-item:{item.kind}:{item.id}",
        "kind": item.kind,
        "name": item.name,
        "name_variants": _compact_names(item, item.name, detail),
        "start_ms": item.start_ms,
        "duration_ms": item.duration_ms,
        "brief": " · ".join(brief_parts),
        "facts": facts,
        "features": features,
        "folded": 0,
        # Operation 是上下文包装层，默认降噪；包含错误时保持展开，避免隐藏观测信号。
        "collapsed": (
            isinstance(item, Operation)
            and bool(children)
            and not source["has_error"]
            and not any(_payload_has_error(child) for child in children)
        ),
        "children": children,
        **source,
    }


def _turn_payload(
    context: TraceContext,
    turn: AgentTurn,
    index: int,
    findings: dict[str, list[Finding]],
) -> dict[str, Any]:
    children = [_item_payload(context, item, findings) for item in turn.items]
    sources = turn.source_node_ids or tuple(
        dict.fromkeys(node_id for item in turn.items for node_id in _item_sources(item))
    )
    name = turn.name or f"Turn {index + 1}"
    return {
        "node_id": f"agent-turn:{turn.id}",
        "kind": "agent-turn",
        "name": name,
        "name_variants": _compact_names(turn, name),
        "start_ms": turn.start_ms,
        "duration_ms": turn.duration_ms,
        "brief": f"{len(turn.items)} items",
        "facts": _facts(turn.status, turn.attributes, sources),
        "features": {},
        "folded": 0,
        "children": children,
        **_source_payload(context, sources, findings, turn.status),
    }


def _item_sources(item: TurnItem) -> tuple[str, ...]:
    nested_runs = item.agent_runs if isinstance(item, ToolCall | Operation) else ()
    operations = item.operations if isinstance(item, Operation) else ()
    return tuple(
        dict.fromkeys(
            (
                *item.source_node_ids,
                *(node_id for child in operations for node_id in _item_sources(child)),
                *(node_id for run in nested_runs for node_id in _run_sources(run)),
            )
        )
    )


def _run_sources(run: AgentRun) -> tuple[str, ...]:
    if run.source_node_ids:
        return run.source_node_ids
    return tuple(
        dict.fromkeys(
            node_id
            for item in run.items
            for node_id in (
                _item_sources(item)
                if isinstance(item, Operation)
                else tuple(
                    node_id for turn_item in item.items for node_id in _item_sources(turn_item)
                )
            )
        )
    )


def _run_payload(
    context: TraceContext,
    run: AgentRun,
    findings: dict[str, list[Finding]],
) -> dict[str, Any]:
    children = []
    turn_index = 0
    for item in run.items:
        if isinstance(item, Operation):
            children.append(_item_payload(context, item, findings))
        else:
            children.append(_turn_payload(context, item, turn_index, findings))
            turn_index += 1
    sources = _run_sources(run)
    operation_count = sum(isinstance(item, Operation) for item in run.items)
    return {
        "node_id": f"agent-run:{run.id}",
        "kind": "agent-run",
        "name": run.name,
        "name_variants": _compact_names(run, run.name),
        "start_ms": run.start_ms,
        "duration_ms": run.duration_ms,
        "brief": f"{turn_index} turns · {operation_count} operations",
        "facts": _facts(run.status, run.attributes, sources),
        "features": {},
        "folded": 0,
        "children": children,
        **_source_payload(context, sources, findings, run.status),
    }


def agent_run_roots(
    context: TraceContext,
    ir: AgentRunIR,
    findings: dict[str, list[Finding]],
) -> list[dict[str, Any]]:
    """Render AgentRun IR into the viewer's language-neutral hierarchical payload."""
    return [_run_payload(context, run, findings) for run in ir.runs]
