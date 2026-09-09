"""ingest —— 主链入口：raw span → Node 模型。

`sources/`（可插拔 span 源：jaeger 文件 / OpenSearch）取回 NormSpan；`load` 嗅探文件形态、
`assemble` 做 fusion（span 熔成 node + per-kind 投影烤进 brief）。**assemble 是整个框架
唯一的领域边界**——下游 analyze/view/corpus 一律只见通用 Node 模型，零 kind 依赖。
"""

from __future__ import annotations

from trace_harness.ingest.assemble import assemble as assemble
from trace_harness.ingest.load import build_context as build_context
from trace_harness.ingest.load import build_context_from_spans as build_context_from_spans
from trace_harness.ingest.sources.jaeger_file import load_jaeger_file as load_jaeger_file
