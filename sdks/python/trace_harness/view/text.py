"""调用栈文本视图——facet 引擎产 DisplayNode，这里序列化成框线树（├─ └─）。

与 markdown 树（`engine.to_md_lines`）同源 DisplayNode、只是字符风格不同：那个给文档/agent 读，
这个给终端。走 facet 引擎，故 http 等也按 facet 折叠（与 md/callstack 一致）。Finding 由 diagnose
产出上色；未跑 diagnose 时，node 自带 error 仍内联（DisplayNode 只装显示，error 经 ctx 回查）。
"""

from __future__ import annotations

from trace_harness.model.context import TraceContext
from trace_harness.model.node import Finding
from trace_harness.view.display import DisplayNode
from trace_harness.view.engine import render

_SEV_ICON = {"error": "✗", "warn": "▲", "info": "·"}


def render_text(ctx: TraceContext, findings: dict[str, list[Finding]] | None = None) -> str:
    findings = findings or {}
    roots = render(ctx.view(), findings)  # DisplayNode 树（facet 折叠 http 等）
    by_id = ctx.view().by_id
    lines: list[str] = [f"trace {ctx.trace_id}  ({len(ctx.nodes)} nodes)"]

    def emit(d: DisplayNode, prefix: str, is_last: bool, is_root: bool) -> None:
        connector = "" if is_root else ("└─ " if is_last else "├─ ")
        label = f"[{d.kind}] {d.name}" if d.kind else d.name  # 合成行（折叠/聚合）无 kind
        head = f"{prefix}{connector}{label}"
        brief = "  ".join(f"{f.label}={f.value}" for f in d.brief)
        if brief:
            head += f"   {brief}"
        lines.append(head)
        child_prefix = prefix + ("" if is_root else ("   " if is_last else "│  "))
        # node 自带 error：仅在未跑 diagnose（无 error 源 Finding）时内联，避免与 Finding 重复
        node = by_id.get(d.node_ids[0]) if d.node_ids else None
        if node is not None and node.has_error and not any(m.source == "error" for m in d.findings):
            err = node.error_text
            lines.append(f"{child_prefix}   ✗ {err}" if err else f"{child_prefix}   ✗ error")
        for m in d.findings:
            icon = _SEV_ICON.get(m.severity, "·")
            note = f" {m.note}" if m.note else ""
            lines.append(f"{child_prefix}   {icon} [{m.source}]{note}")
        for i, c in enumerate(d.children):
            emit(c, child_prefix, i == len(d.children) - 1, is_root=False)

    for i, r in enumerate(roots):
        emit(r, "", i == len(roots) - 1, is_root=True)
    return "\n".join(lines)
