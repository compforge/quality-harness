"""Shared protocol fixtures run against the public harness without AS contributions."""

import json
from pathlib import Path

import pytest

from trace_harness import TraceContributions, TraceHarness
from trace_harness.kinds import genai
from trace_harness.model.span import NormSpan

CASES = json.loads(
    (Path(__file__).parents[4] / "conformance/trace/cases/http-detectors.json").read_text()
)


@pytest.mark.parametrize("case", CASES, ids=lambda case: case["name"])
def test_http_detector_conformance(case):
    harness = TraceHarness(TraceContributions(specs=tuple(genai.specs())))
    spans = {
        item["span_id"]: NormSpan(**item, has_error=False, raw={"traceID": case["name"]})
        for item in case["spans"]
    }
    context = harness.assemble(spans)
    findings = [
        finding
        for group in harness.diagnose(context).values()
        for finding in group
        if finding.source.startswith("http_")
    ]
    if limits := case.get("limits"):
        for source, limit in limits.items():
            hits = [finding for finding in findings if finding.source == source]
            assert len(hits) == limit["count"]
            assert f"共 {limit['total']} 条" in hits[0].note
        return
    assert len(findings) == len(case["expected"])
    for expected in case["expected"]:
        hits = [
            finding
            for finding in findings
            if finding.source == expected["source"]
            and finding.data["span_ids"] == expected["span_ids"]
        ]
        assert len(hits) == 1
        hit = hits[0]
        actual = {"source": hit.source, "severity": hit.severity, **hit.data}
        assert {key: actual[key] for key in expected} == expected
        assert hit.ref in context.view().by_id
        if hit.source == "http_serial_same_api":
            assert hit.data["http_total_ms"] + hit.data["gap_ms"] == hit.data["wall_ms"]
        assert "secret" not in hit.note
    html = harness.render_interactive(context, harness.diagnose(context))
    for finding in findings:
        assert finding.source in html
