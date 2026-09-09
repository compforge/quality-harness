"""ViewState —— treecli 的有状态视图（展开集 + focus 根），落盘 `<nodes>.view.json`。

缩略图 = 空展开集；`expand/collapse/focus` 改状态后重渲染（同一 render 函数）。AI 把
「想看 X」翻成 selector，本模块解析成 node_id：
  - handle           node_id 全量或前缀（hex span / `g…` 服务组）
  - kind:NAME        按 kind
  - name 子串        节点名包含
  - error            所有出错 node
  - slowest / topN   按耗时序数
  - cost>30s         谓词

`iso_sig` 给同构兄弟（如一次 eval 跑出的 N 个同形 default.agent）一个浅签名，让缩略图
把它们折叠成一行 `×N`，展开其中某个 handle 才拆出来。
"""

from __future__ import annotations

import hashlib
import json
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path

from trace_harness.model.node import Node
from trace_harness.model.viewtree import ViewTree

_HEX = set("0123456789abcdef")


def handle(n: Node) -> str:
    """节点的稳定短 handle：hex span-id 取前 8 位；非 hex（服务组 svc:…）取 `g`+短哈希。"""
    nid = n.node_id
    if len(nid) >= 8 and all(c in _HEX for c in nid):
        return nid[:8]
    return "g" + hashlib.sha1(nid.encode()).hexdigest()[:7]


@dataclass
class ViewState:
    expanded: set[str] = field(default_factory=set)  # 展开了子节点的 node_id
    focus: str | None = None  # 把某 node 当唯一根放大

    @classmethod
    def load(cls, path: str | Path) -> ViewState:
        p = Path(path)
        if not p.exists():
            return cls()
        d = json.loads(p.read_text(encoding="utf-8"))
        return cls(expanded=set(d.get("expanded") or []), focus=d.get("focus"))

    def save(self, path: str | Path) -> None:
        payload = {"expanded": sorted(self.expanded), "focus": self.focus}
        Path(path).write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")


def _parse_dur(s: str) -> float:
    s = s.strip().lower()
    if s.endswith("ms"):
        return float(s[:-2])
    if s.endswith("s"):
        return float(s[:-1]) * 1000
    return float(s)


def resolve_selector(nodes: list[Node], sel: str) -> list[str]:
    """selector → 命中的 node_id 列表（空 = 没匹配上）。"""
    by_id = {n.node_id: n for n in nodes}
    if sel in by_id:
        return [sel]
    by_handle = {handle(n): n.node_id for n in nodes}
    if sel in by_handle:
        return [by_handle[sel]]
    if sel == "error":
        return [n.node_id for n in nodes if n.has_error]
    if sel == "slowest":
        return [max(nodes, key=lambda n: n.duration_ms).node_id] if nodes else []
    if sel.startswith("top") and sel[3:].isdigit():
        k = int(sel[3:])
        return [n.node_id for n in sorted(nodes, key=lambda n: -n.duration_ms)[:k]]
    if sel.startswith("kind:"):
        k = sel[5:]
        return [n.node_id for n in nodes if n.kind == k]
    if sel.startswith("cost>"):
        try:
            ms = _parse_dur(sel[5:])
        except ValueError:
            return []
        return [n.node_id for n in nodes if n.duration_ms > ms]
    # name 子串
    hits = [n.node_id for n in nodes if sel in n.name]
    if hits:
        return hits
    # handle / node_id 前缀兜底
    return [nid for nid in by_id if nid.startswith(sel)]


def iso_sig(view: ViewTree, n: Node, depth: int = 2) -> tuple:
    """同构浅签名：(kind, name, 子节点签名多重集)。depth 限界，避免在大树上递归爆栈。"""
    if depth <= 0:
        return (n.kind, n.name)
    child_sigs = Counter(iso_sig(view, c, depth - 1) for c in view.children(n))
    return (n.kind, n.name, tuple(sorted(child_sigs.items())))
