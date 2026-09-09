"""Language-neutral Trace Harness analysis IR projection."""

from __future__ import annotations

from trace_harness.model.context import TraceContext
from trace_harness.model.node import Finding

SCHEMA = "trace-harness/analysis@1"


def analysis_snapshot(
    context: TraceContext,
    findings: dict[str, list[Finding]] | None = None,
) -> dict:
    """Project runtime objects into the canonical JSON-compatible analysis IR."""
    nodes = sorted(context.nodes, key=lambda node: (node.start_ms, node.node_id))
    flattened = sorted(
        (finding for group in (findings or {}).values() for finding in group),
        key=lambda finding: (
            finding.scope,
            finding.ref or "",
            finding.source,
            finding.severity,
            finding.note,
        ),
    )
    return {
        "schema": SCHEMA,
        "trace_id": context.trace_id,
        "span_count": context.span_count,
        "nodes": [
            {
                "node_id": node.node_id,
                "parent_node_id": node.parent_node_id,
                "kind": node.kind,
                "name": node.name,
                "start_ms": node.start_ms,
                "duration_ms": node.duration_ms,
                "service": node.service,
                "primary_span_id": node.primary_span_id,
                "span_ids": list(node.span_ids),
                "error_span_ids": list(node.error_span_ids),
                "error_text": node.error_text,
                "facts": node.facts,
                "brief": [
                    {
                        "label": item.label,
                        "value": item.value,
                        "emphasis": item.emphasis,
                    }
                    for item in node.brief
                ],
            }
            for node in nodes
        ],
        "findings": [
            {
                "ref": finding.ref,
                "source": finding.source,
                "severity": finding.severity,
                "scope": finding.scope,
                "rank": finding.rank,
                "note": finding.note,
                "data": finding.data,
                "symptoms": list(finding.symptoms),
                "causes": list(finding.causes),
            }
            for finding in flattened
        ],
    }
