"""NormSpan：归一后的单个物理 span（kind 无关骨架字段统一形态）。

刻意**不带语义 kind 字段**——span 是哪种逻辑事件（model-call/tool-call/…）由
`spec.matches` 在 assemble 时裁决，识别职责不泄漏进采集层（doc §3.1 的事故教训）。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


def _num(v: Any) -> float | None:
    """attr 值 → 数值；遥测里 int64/float 可能存成字符串，统一容错。非数返回 None。"""
    if v is None or v == "":
        return None
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


@dataclass
class NormSpan:
    """归一后的物理 span。raw 留作原文溯源（错误事件、to_curl）。"""

    span_id: str
    parent_span_id: str | None
    name: str  # operationName
    start_ms: float  # wall-clock start（epoch ms）；时间统一 ms，源单位换算收口在 source
    dur_ms: float
    service: str | None
    has_error: bool
    attrs: dict  # 扁平 attr（tags → {key: value}）
    raw: dict  # 原始 ES jaeger-span 文档，溯源用
    # logs 里抽出的异常事件 [{type, message, stacktrace}]
    error_events: list = field(default_factory=list)
    # 全部 span event：[{name, timestamp_ms, attrs}]；error_events 是其中异常子集
    events: list = field(default_factory=list)

    @property
    def end_ms(self) -> float:
        return self.start_ms + self.dur_ms

    def attr(self, *names: str, default: Any = None) -> Any:
        """多名取值：新旧埋点字段名在调用点列出，第一个命中的非空值胜出。"""
        for n in names:
            v = self.attrs.get(n)
            if v not in (None, ""):
                return v
        return default

    def num(self, *names: str) -> float | None:
        return _num(self.attr(*names))


def error_text(s: NormSpan) -> str:
    """span 的错误原文摘要（异常事件优先，否则 otel.status_description）。

    assemble 烤 Node.error_text 与 ctx.error_text 共用此实现，避免两处口径漂移。
    """
    if s.error_events:
        e0 = s.error_events[0]
        return f"{e0.get('type', '')}: {e0.get('message', '')}".strip(": ")
    return str(s.attrs.get("otel.status_description") or "")
