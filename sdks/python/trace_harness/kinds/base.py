"""BaseKind 公共件 + service 残余组 spec。

duration_ms 是每个 kind 都有的基线度量——放这里统一供给，避免各 kind 各自泄漏一份
（曾经 duration_ms 从 model-call handler 全局漏出、污染别的 kind 的事故）。

残余（未命中任何 kind、也未被认领为卫星的 span）不再每个各自成节点——大 trace 里几千
条 redis/内部 span 会把树打爆；assemble 按 (最近 primary 宿主, service) 聚成一个
service 组节点（count/sum_ms），低分辨率但保留"这段时间这个服务在干活"的事实。
"""

from __future__ import annotations

from trace_harness.model.node import Field, Node
from trace_harness.model.spec import KindSpec

SERVICE_KIND = "service"


def duration_metric() -> dict:
    """每个 kind 的基线度量：wall-clock duration（ms）。

    outlier 引擎按 (kind,name,metric) 建基线。
    """
    return {"duration_ms": lambda n: n.facts.get("duration_ms")}


def _fmt_ms(ms: float | None) -> str:
    if ms is None:
        return "?"
    if ms >= 1000:
        return f"{ms / 1000:.2f}s"
    return f"{ms:.0f}ms"


def _fmt_bytes(n: int | None) -> str:
    """人读字节数，_fmt_ms 的配套（tool result 大小等）。"""
    if n is None:
        return "?"
    if n >= 1024 * 1024:
        return f"{n / (1024 * 1024):.1f}MB"
    if n >= 1024:
        return f"{n / 1024:.1f}KB"
    return f"{n}B"


def service_spec() -> KindSpec:
    """残余 service 组的展示 spec：×N + sum。不认领原始 span（组由 assemble 残余聚合步合成）；
    刻意不带 metrics——低分辨率聚合上判离群无意义（老体系 exclude_kinds=("service",) 的教训）。
    """

    def project(n: Node) -> list[Field]:
        f = n.facts
        return [
            Field(label="×", value=str(f.get("count", "?")), emphasis="dim"),
            Field(label="sum", value=_fmt_ms(f.get("sum_ms")), emphasis="dim"),
        ]

    return KindSpec(kind=SERVICE_KIND, matches=lambda s: False, project=project)
