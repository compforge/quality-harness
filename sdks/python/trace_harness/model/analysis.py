"""Language-neutral Trace Harness analysis IR projection."""

from __future__ import annotations

import json
from dataclasses import asdict
from pathlib import Path

from trace_harness.analyze.context import AnalysisContext
from trace_harness.model.context import TraceContext
from trace_harness.model.measurement import CallSource, Measurement, Measurements, MeasurementSpec
from trace_harness.model.node import Field, Finding, Node

SCHEMA = "trace-harness/analysis@2"


def analysis_snapshot(
    analysis: AnalysisContext,
) -> dict:
    """Project runtime objects into the canonical JSON-compatible analysis IR."""
    context = analysis.trace
    findings = analysis.findings
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
        "measurements": json.loads(json.dumps(asdict(analysis.measurements))),
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


def dump_analysis(analysis: AnalysisContext, path: str | Path) -> Path:
    path = Path(path)
    # Execution metadata belongs to saved analysis, not the language-neutral
    # semantic snapshot used to compare findings/measurements across runtimes.
    payload = {**analysis_snapshot(analysis), "detector_runs": list(analysis.detector_runs)}
    path.write_text(
        json.dumps(payload, ensure_ascii=False, allow_nan=False),
        encoding="utf-8",
    )
    if analysis.trace.spans:
        coverage = {
            sid: {"fields": span.loaded_fields, "failed": span.field_errors}
            for sid, span in analysis.trace.spans.items()
        }
        path.with_suffix(".evidence.json").write_text(json.dumps(coverage, ensure_ascii=False))
    return path


def load_analysis(path: str | Path) -> AnalysisContext:
    """Reload saved results for offline rendering, without running any extensions."""
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    if data.get("schema") != SCHEMA:
        raise ValueError(f"not a {SCHEMA} analysis file: {path}")
    nodes = [
        Node(**{**item, "brief": [Field(**value) for value in item["brief"]]})
        for item in data["nodes"]
    ]
    trace = TraceContext(data["trace_id"], {}, nodes, {}, observed_span_count=data["span_count"])
    payload = data["measurements"]
    measurements = Measurements(
        [
            MeasurementSpec(**{**item, "dimensions": tuple(item["dimensions"])})
            for item in payload["specs"]
        ],
        [
            CallSource(**{**item, "span_ids": tuple(item["span_ids"])})
            for item in payload["sources"]
        ],
        {
            node_id: [Measurement(**item) for item in results]
            for node_id, results in payload["results"].items()
        },
    )
    findings: dict[str, list[Finding]] = {}
    for item in data["findings"]:
        finding = Finding(
            **{**item, "symptoms": tuple(item["symptoms"]), "causes": tuple(item["causes"])}
        )
        findings.setdefault(finding.node_id, []).append(finding)
    return AnalysisContext(
        trace,
        measurements,
        {key: tuple(value) for key, value in findings.items()},
        detector_runs=tuple(data.get("detector_runs", ())),
    )
