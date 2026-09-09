"""TraceView —— 渲染/探索的可序列化 IR（doc 立场①「树是视图期产物」的落盘形态）。

assemble 把 per-kind 投影一次烤进 `Node.brief` 之后，渲染只需要一个窄「可渲染面」：
`trace_id / nodes / span_count / view()`。`TraceContext`（内存事实源）与 `TraceView`
（落盘 `nodes.json`）都满足这个面——**同一渲染接口，两种 transport**。treecli 读
`nodes.json` 即得 TraceView，零域依赖、不碰 raw span，只在导航时按 span 指针回原文。

`nodes.json` 是 assemble（域感知：span→node + 投影）与 explore（通用：逛树）之间唯一契约：
左侧随便换 kind/数据源，只要还吐这个 schema，treecli 一行不用改。
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Protocol, runtime_checkable

from trace_harness.model.node import Field, Node
from trace_harness.model.viewtree import ViewTree, build_view

SCHEMA = "trace-harness/nodes@1"


@runtime_checkable
class Renderable(Protocol):
    """渲染器/explore 依赖的结构面——ctx 与 TraceView 都满足（接口隔离）。"""

    trace_id: str
    nodes: list[Node]
    span_count: int

    def view(self) -> ViewTree: ...


@dataclass
class TraceView:
    """nodes.json 反序列化后的可渲染体；结构面与 TraceContext 对齐，但**不含 raw span**。"""

    trace_id: str
    nodes: list[Node]
    span_count: int
    _view: ViewTree | None = field(default=None, repr=False)

    def view(self) -> ViewTree:
        if self._view is None:
            self._view = build_view(self.nodes)
        return self._view

    @classmethod
    def from_context(cls, ctx: Renderable) -> TraceView:
        """内存 ctx → 渲染体（同进程直接用，不必落盘）。"""
        return cls(trace_id=ctx.trace_id, nodes=list(ctx.nodes), span_count=ctx.span_count)


def _node_to_dict(n: Node) -> dict:
    # 只序列化「结构 + 烤好的 brief + span 指针」——原文不进 IR（peek 时按指针回 raw 快照）。
    return {
        "node_id": n.node_id,
        "parent_node_id": n.parent_node_id,
        "kind": n.kind,
        "name": n.name,
        "start_ms": n.start_ms,
        "duration_ms": n.duration_ms,
        "service": n.service,
        "primary_span_id": n.primary_span_id,
        "span_ids": n.span_ids,
        "error_span_ids": n.error_span_ids,
        "error_text": n.error_text,
        "facts": n.facts,
        "brief": [[f.label, f.value, f.emphasis] for f in n.brief],
    }


def _node_from_dict(d: dict) -> Node:
    return Node(
        kind=d["kind"],
        name=d["name"],
        primary_span_id=d["primary_span_id"],
        span_ids=list(d.get("span_ids") or []),
        facts=d.get("facts") or {},
        start_ms=d["start_ms"],
        duration_ms=d["duration_ms"],
        service=d.get("service"),
        node_id=d["node_id"],
        parent_node_id=d.get("parent_node_id"),
        error_span_ids=list(d.get("error_span_ids") or []),
        brief=[Field(label=a, value=b, emphasis=e) for a, b, e in (d.get("brief") or [])],
        error_text=d.get("error_text") or "",
    )


def dump_view(view: Renderable, path: str | Path) -> Path:
    """ctx/TraceView → nodes.json（自包含 IR）。assemble 侧产出，explore 侧消费。"""
    path = Path(path)
    payload = {
        "schema": SCHEMA,
        "trace_id": view.trace_id,
        "span_count": view.span_count,
        "nodes": [_node_to_dict(n) for n in view.nodes],
    }
    path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    return path


def load_view(path: str | Path) -> TraceView:
    d = json.loads(Path(path).read_text(encoding="utf-8"))
    if d.get("schema") != SCHEMA:
        raise ValueError(f"not a {SCHEMA} nodes file: {path}")
    return TraceView(
        trace_id=d.get("trace_id") or "?",
        nodes=[_node_from_dict(x) for x in (d.get("nodes") or [])],
        span_count=int(d.get("span_count") or 0),
    )


def is_nodes_file(path: str | Path) -> bool:
    """内容嗅探：是本框架的 nodes.json IR，而非 jaeger 快照。"""
    try:
        with open(path, encoding="utf-8") as f:
            return SCHEMA in f.read(256)
    except OSError:
        return False
