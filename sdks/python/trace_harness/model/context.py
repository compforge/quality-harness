"""TraceContext —— 一次 trace 分析的承载对象。

事实源（spans + nodes）+ 语义注册表（specs）+ 运行时（evidence_dir），dispatch 挂这里
（无全局态，Node 保持纯数据）。tree 不在这——视图期按需 `ctx.view()` 现搭。

原文溯源说明：本框架直吃 raw jaeger（无 call-stack 字段级合并），故 `spans[sid].attrs`
**即干净的物理 span 原文**——to_curl 拼错 url 那类合并污染在此架构下结构性消失。lazy
全量原文池（FullAttrsIndex）留到 light/full 两段采集（step 2）才需要，1a 直接读 spans。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from trace_harness.model.node import Node
from trace_harness.model.span import NormSpan
from trace_harness.model.span import error_text as span_error_text
from trace_harness.model.viewtree import ViewTree, build_view


@dataclass
class TraceContext:
    trace_id: str
    spans: dict[str, NormSpan]  # span_id → NormSpan（物理事实源）
    nodes: list[Node]  # 分析本体（平集）
    specs: dict[str, Any]  # 真实 kind → KindSpec（assemble 时按认领建）
    evidence_dir: Path | None = None
    _view: ViewTree | None = field(default=None, repr=False)

    # 渲染面：与 TraceView（落盘 IR）共用的窄属性集（trace_id/nodes/span_count/view()），
    # 渲染器对此 duck-type，故 ctx（内存）与 nodes.json（落盘）走同一渲染路径。
    @property
    def span_count(self) -> int:
        return len(self.spans)

    # —— 视图期树（惰性，仅渲染/火焰/最近祖先用）——
    def view(self) -> ViewTree:
        if self._view is None:
            self._view = build_view(self.nodes)
        return self._view

    # —— 原文溯源 ——
    def raw(self, span_id: str) -> dict:
        s = self.spans.get(span_id)
        return s.raw if s else {}

    def raw_attr(self, span_id: str) -> dict:
        """物理 span 的干净原文 attrs（直吃 raw jaeger，无合并污染）。"""
        s = self.spans.get(span_id)
        return s.attrs if s else {}

    def error_text(self, span_id: str) -> str:
        """span 的错误原文（异常事件优先，否则 otel.status_description）。"""
        s = self.spans.get(span_id)
        return span_error_text(s) if s else ""
