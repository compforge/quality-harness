"""Explicit node measurement preparation; presentation never computes these values."""

import json
from dataclasses import asdict
from math import isfinite

from trace_harness.kinds.http import http_endpoint
from trace_harness.model.context import TraceContext
from trace_harness.model.measurement import Measurement, Measurements, MeasurementSpec

CALL_BREAKDOWN = MeasurementSpec(
    "call_breakdown",
    "node",
    {"window": "ms", "calls": "ms", "groups": "ms"},
    "Clipped calls and operation aggregates through this node's end.",
)


def prepare_call_breakdown(trace: TraceContext, measurements: Measurements, node_id: str) -> None:
    """Store a neutral derived Measurement, retaining source identities and intervals."""
    nodes = trace.tree().by_id
    if node_id not in nodes:
        raise ValueError(f"Trace {trace.trace_id}: node {node_id} not found")
    if measurements.get(node_id, CALL_BREAKDOWN.kind):
        return
    prefix = measurements.get(node_id, "calls_until_node_end")
    if prefix is None:
        return
    values = {}
    if prefix.status == "measured":
        start, end = prefix.evidence.get("start_ms"), prefix.evidence.get("end_ms")
        if (
            prefix.evidence.get("source") != "calls"
            or not all(isinstance(t, (int, float)) and isfinite(t) for t in (start, end))
            or end < start
        ):
            raise ValueError(f"Trace {trace.trace_id}: node {node_id}: invalid measurement window")
        node_calls = {
            call.node_id: f"{call.kind}:{call.id}"
            for call in measurements.sources
            if call.kind != "http"
        }
        calls, groups = [], {}
        for source in measurements.sources:
            if source.start_ms > end or source.end_ms < start:
                continue
            node = nodes.get(source.node_id)
            span_id = (
                source.span_ids[0]
                if source.kind == "http" and source.span_ids
                else node.primary_span_id
                if node
                else ""
            )
            span = trace.spans.get(span_id)
            endpoint = (
                http_endpoint(span) if span and source.kind in {"http", "service-api"} else None
            )
            route = (
                next(
                    (
                        endpoint[2]
                        for sid in source.span_ids[1:]
                        if sid in trace.spans and (endpoint := http_endpoint(trace.spans[sid]))
                    ),
                    None,
                )
                if source.kind == "http"
                else None
            )
            endpoint = (
                http_endpoint(span) if span and source.kind in {"http", "service-api"} else None
            )
            name = (
                f"{endpoint[0]} {route or endpoint[2]}"
                if endpoint
                else node.name
                if node
                else span.name
                if span
                else source.id
            )
            service = (
                span.service if source.kind == "http" and span else node.service if node else ""
            ) or ""
            target = endpoint[1] if endpoint else ""
            parent = node.parent_node_id if node else None
            while parent and parent not in node_calls:
                parent = nodes[parent].parent_node_id if parent in nodes else None
            parent_key = (
                node_calls.get(source.node_id)
                if source.kind == "http" and source.node_id in node_calls
                else node_calls.get(parent)
            )
            key = json.dumps(
                [source.kind, service, target, name], ensure_ascii=False, separators=(",", ":")
            )
            duration = max(0, min(end, source.end_ms) - max(start, source.start_ms))
            call = {
                **asdict(source),
                "span_ids": list(source.span_ids),
                "key": f"{source.kind}:{source.id}",
                "name": name,
                "target": target,
                "service": service,
                "start": max(start, source.start_ms) - start,
                "end": min(end, source.end_ms) - start,
                "duration": duration,
                "clipped": source.start_ms < start or source.end_ms > end,
                "group": key,
            }
            if parent_key is not None:
                call["parent"] = parent_key
            calls.append(call)
            group = groups.setdefault(
                key,
                {
                    "key": key,
                    "kind": source.kind,
                    "service": service,
                    "target": target,
                    "name": name,
                    "count": 0,
                    "total": 0,
                    "max": 0,
                    "average": 0,
                },
            )
            group["count"] += 1
            group["total"] += duration
            group["max"] = max(group["max"], duration)
            group["average"] = group["total"] / group["count"]
        values = {
            "window": {"start_ms": start, "end_ms": end, "elapsed_ms": end - start},
            "calls": calls,
            "groups": sorted(groups.values(), key=lambda g: (-g["total"], g["key"])),
        }
    if not any(spec.kind == CALL_BREAKDOWN.kind for spec in measurements.specs):
        measurements.specs.append(CALL_BREAKDOWN)
    measurements.nodes.setdefault(node_id, []).append(
        Measurement(
            CALL_BREAKDOWN.kind,
            node_id,
            prefix.status,
            values,
            {"measurement": "calls_until_node_end", "node_id": node_id},
            prefix.error,
        )
    )


def saved_call_breakdown(measurements: Measurements, node_id: str) -> dict | None:
    saved = measurements.get(node_id, CALL_BREAKDOWN.kind)
    return (
        {
            "status": saved.status,
            "error": saved.error,
            "window": saved.values.get("window"),
            "calls": saved.values.get("calls", []),
            "groups": saved.values.get("groups", []),
        }
        if saved
        else None
    )
