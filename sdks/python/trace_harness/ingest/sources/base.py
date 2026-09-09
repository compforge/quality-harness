"""Source —— 采集协议：唯一知道后端的层，把各种遥测存储归一成 NormSpan，喂给 assemble。

两个动作分清职责：
- `select(query)`：**cohort 选择**——按条件在后端查命中 span（轻投影，不拉巨型 body），
  返回投影过的 NormSpan 列表（可跨多 trace）。
- `fetch(trace_id, fidelity)`：**单 trace 取全量**——一条 trace 的全部 span，看调用栈用。
- `raw_attrs(trace_id, span_id)`：懒拉单 span 的巨型原文（probe / to_curl 才碰）。

`SpanQuery` 是后端无关的选择语言；各 Source（opensearch / jaeger_file / driver）把它翻译成
自己后端的查询。**env 发现不在这层**——Source 只收 host/index/auth 这类纯参数，环境注册表、
凭据解析留消费方（如 trace-as）装配。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Protocol, runtime_checkable

from trace_harness.model.span import NormSpan

# 巨型 attr 的默认剥除集——light 采集剥掉它们，度量类 facts（input.length 等数值）不受损。
# 真正的 prompt/completion/body 原文走 raw_attrs 懒拉，不进 cohort 内存。
DEFAULT_DROP_ATTRS: tuple[str, ...] = (
    "gen_ai.input.messages",
    "gen_ai.prompt",
    "gen_ai.completion",
    "gen_ai.output.messages",
    "gen_ai.http.request.body",
    "gen_ai.http.response.body",
)


class Fidelity(str, Enum):
    """取数保真度——漏斗从粗到细：

    - SKELETON：只骨架（id/parent/start/dur/service/error），连 facts attr 都不带，最省。
    - LIGHT：骨架 + 度量类 attr（剥掉 DEFAULT_DROP_ATTRS 的巨型原文）——看树/建 facts 够用。
    - FULL：全量 attr（含 prompt/body 原文）——probe / to_curl / 内容下钻才用。
    """

    SKELETON = "skeleton"
    LIGHT = "light"
    FULL = "full"


@dataclass
class SpanQuery:
    """后端无关的「选哪些 span」。各 Source 翻译成自己后端的查询。

    `attr_eq` 是 nested-tag 等值过滤（如 `{"error.type": "ModelTotalTimeoutError"}`）——
    域 Selector（trace-as 的 error_code）把语义条件编译成它。
    """

    attr_eq: dict[str, str] = field(default_factory=dict)
    trace_ids: list[str] | None = None  # 直接圈定 trace（seed / 已知 id 列表）
    error_only: bool = False
    service: str | None = None
    since_ms: float | None = None
    until_ms: float | None = None
    limit: int = 1000
    drop_attrs: tuple[str, ...] = DEFAULT_DROP_ATTRS  # select 投影时剥除的巨型 attr


@runtime_checkable
class Source(Protocol):
    """采集协议。实现：JaegerFileSource（离线）/ OpenSearchSource（在线）/ DriverSource（主动）。"""

    def select(self, query: SpanQuery) -> list[NormSpan]:
        """按条件查命中 span（轻投影，可跨多 trace）。cohort 发现入口。"""
        ...

    def fetch(self, trace_id: str, fidelity: Fidelity = Fidelity.LIGHT) -> dict[str, NormSpan]:
        """取一条 trace 的全部 span（看调用栈 / Tier-2 建模）。"""
        ...

    def raw_attrs(self, trace_id: str, span_id: str) -> dict:
        """懒拉单 span 的全量原文 attrs（probe / to_curl）。默认实现可经 fetch(FULL) 兜底。"""
        ...


def strip_attrs(span: NormSpan, drop: tuple[str, ...]) -> NormSpan:
    """剥掉巨型 attr（light 投影）；原 raw 保留，溯源时仍可经 raw_attrs 回。"""
    if not drop:
        return span
    span.attrs = {k: v for k, v in span.attrs.items() if k not in drop}
    return span
