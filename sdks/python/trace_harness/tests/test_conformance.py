"""Shared language-neutral Trace Harness conformance case."""

from __future__ import annotations

import json
from pathlib import Path

from trace_harness import TraceContributions, TraceHarness, analysis_snapshot
from trace_harness.kinds import genai

ROOT = Path(__file__).parents[4]
RAW = ROOT / "conformance" / "trace" / "fixtures" / "genai-basic.jsonl"
EXPECTED = ROOT / "conformance" / "trace" / "cases" / "genai-basic.analysis.json"


async def test_shared_genai_analysis_ir():
    harness = TraceHarness(TraceContributions(specs=tuple(genai.specs())))
    context = harness.build_context(RAW)
    actual = analysis_snapshot(await harness.analyze(context))

    assert actual == json.loads(EXPECTED.read_text(encoding="utf-8"))


async def test_managed_loading_preserves_shared_ir(tmp_path):
    from trace_harness import LoadConfig
    from trace_harness.ingest.sources.jaeger_file import JaegerFileSource

    expected = json.loads(EXPECTED.read_text(encoding="utf-8"))
    harness = TraceHarness(TraceContributions(specs=tuple(genai.specs())))
    for lazy in (True, False):
        async with harness.open(
            JaegerFileSource(RAW), work_dir=tmp_path / str(lazy), config=LoadConfig(lazy=lazy)
        ) as session:
            dataset = await session.select()
            async with session.tree(dataset, next(dataset.members())) as context:
                result = await session.analyze(context)
                await session.prepare_view(result, full=True)
                assert analysis_snapshot(result) == expected
