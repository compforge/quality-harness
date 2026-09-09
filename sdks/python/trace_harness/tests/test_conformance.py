"""Shared language-neutral Trace Harness conformance case."""

from __future__ import annotations

import json
from pathlib import Path

from trace_harness import TraceContributions, TraceHarness, analysis_snapshot
from trace_harness.kinds import genai

ROOT = Path(__file__).parents[4]
RAW = ROOT / "conformance" / "trace" / "fixtures" / "genai-basic.jsonl"
EXPECTED = ROOT / "conformance" / "trace" / "cases" / "genai-basic.analysis.json"


def test_shared_genai_analysis_ir():
    harness = TraceHarness(TraceContributions(specs=tuple(genai.specs())))
    context = harness.build_context(RAW)
    actual = analysis_snapshot(context, harness.diagnose(context))

    assert actual == json.loads(EXPECTED.read_text(encoding="utf-8"))
