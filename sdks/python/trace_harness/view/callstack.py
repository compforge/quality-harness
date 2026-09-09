"""调用栈渲染的共享呈现辅助：findings 块 / Finding 上色 / 字段格式化 / 剪枝阈值。

渲染入口（facet 引擎 → DisplayNode → 文本 / markdown / treecli）在 `engine.py`；各序列化器复用
这里的辅助，不再各自实现一套遍历/折叠/上色。判读与展示解耦：Finding 由 diagnose 产出，这里只读
Finding 上色 + 末尾 findings 块。
"""

from __future__ import annotations

from trace_harness.model.context import TraceContext
from trace_harness.model.node import Field, Finding, Node

_SEV_ORDER = {"error": 3, "warn": 2, "info": 1}
_SEV_FLAG = {"error": "🔴 ", "warn": "🟡 ", "info": "🔵 "}


def findings_block(
    ctx: TraceContext, findings: dict[str, list[Finding]] | None, *, problem_floor: str = "warn"
) -> str:
    """按 source 分组、每组 top 5——避免某类（如 outlier）刷屏淹没其它判读。

    `problem_floor` 以下严重度的 finding（默认 info，如 biz 结构/骨干 signal）只驱动 salience/keep，
    **不进问题清单**——结构信号不该当问题刷屏（severity 分级 salience）。"""
    sev = {"error": 0, "warn": 1, "info": 2}
    floor = sev.get(problem_floor, 1)
    flat = [f for fs in (findings or {}).values() for f in fs if sev.get(f.severity, 0) <= floor]
    if not flat:
        return "findings: 0"
    by_src: dict[str, list[Finding]] = {}
    for f in flat:
        by_src.setdefault(f.source, []).append(f)
    view = ctx.view()
    lines = [f"findings: {len(flat)}（{len(by_src)} 类）"]
    for src in sorted(by_src, key=lambda s: min(sev.get(f.severity, 9) for f in by_src[s])):
        fs = by_src[src]
        lines.append(f"  {src} ×{len(fs)}:")
        for f in fs[:5]:
            n = view.by_id.get(f.ref)
            nm = f"{n.kind} {n.name}" if n else f.ref
            rank = f" #{f.rank}" if f.rank is not None else ""
            note = f"  — {f.note}" if f.note else ""
            lines.append(f"    [{f.severity}]{rank} → {nm}{note}")
    return "\n".join(lines)


def _md_field(f: Field) -> str:
    if f.emphasis == "strong":
        return f"{f.label}=**{f.value}**"
    if f.emphasis == "dim":
        return f"{f.label}=_{f.value}_"
    return f"{f.label}={f.value}"


def _prune_cutoff(skel: list[Node], keep: int = 80) -> float:
    """剪枝阈值：保留耗时最大的约 keep 个骨架节点（+ 所有错误/判读节点）。"""
    durs = sorted((n.duration_ms for n in skel), reverse=True)
    return durs[min(len(durs) - 1, keep)] if durs else 0.0
