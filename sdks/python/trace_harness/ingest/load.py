"""build_context —— 离线文件 → TraceContext（构建与判读分离）。

刻意不在这里跑 diagnose：看树零副作用；判读（尤其 probe 落盘）由 CLI 按需调
`diagnose(ctx, probes=...)`（1b 落地）。
"""

from __future__ import annotations

from pathlib import Path

from trace_harness.ingest.assemble import assemble
from trace_harness.ingest.sources.jaeger_file import load_jaeger_file
from trace_harness.kinds import genai
from trace_harness.model.context import TraceContext
from trace_harness.model.span import NormSpan
from trace_harness.model.spec import SpecSet


def build_context_from_spans(
    spans: dict[str, NormSpan], specset: SpecSet | None = None
) -> TraceContext:
    """NormSpan 集 → TraceContext（建模，不判读）。Source 无关入口——Cohort 逐 trace 调它。

    specset 缺省用通用 GenAI specs；消费方传 `merge(genai.specs(), as_kinds.specs())` 叠加域 kind。
    """
    return assemble(spans, specset or genai.specs())


def build_context(path: str | Path, specset: SpecSet | None = None) -> TraceContext:
    """ES jaeger-span `.jsonl` 路径 → TraceContext。文件入口，内部走 build_context_from_spans。"""
    ctx = build_context_from_spans(load_jaeger_file(path), specset)
    ctx.evidence_dir = Path(path).parent / ctx.trace_id
    return ctx
