"""render_explore —— treecli 的渲染核：按 ViewState 出「缩略图 → 逐步展开」的调用树。

与 `callstack.render_callstack`（一次性、按耗时全局剪枝）的分工：那个是「一屏看全貌」，
这个是**有状态探索**——默认只铺骨架顶层（空展开集 = 二三十行缩略图），`expand` 把某子树
展开一层，同一 render 重画。两条策略，共享同一份烤好的 `Node.brief` 和 viewtree 遍历。

折叠两类信息，都留 handle 供下钻：
  - 未展开的有子节点 → 一行 `⊕<handle> [N kids · 耗时 · 错误]`
  - 同构兄弟（一次 eval 跑出的 N 个同形 agent）→ 一行 `×N  ⊕h1,h2,…`，展开某 handle 才拆开
"""

from __future__ import annotations

from trace_harness.kinds.base import SERVICE_KIND, _fmt_ms
from trace_harness.model.ir import Renderable
from trace_harness.model.node import Node
from trace_harness.model.viewtree import ViewTree
from trace_harness.view.state import ViewState, handle, iso_sig


def _brief(n: Node) -> str:
    return "  ".join(f"{f.label}={f.value}" for f in n.brief)


def _subtree_stats(view: ViewTree, n: Node) -> tuple[int, float, int]:
    """子树 (节点数, wall-clock duration 总和, 含错误的节点数)——折叠摘要用。"""
    cnt, total, errs = 1, n.duration_ms, (1 if n.has_error else 0)
    for c in view.children(n):
        cc, ct, ce = _subtree_stats(view, c)
        cnt += cc
        total += ct
        errs += ce
    return cnt, total, errs


def _emit_node(
    view: ViewTree, n: Node, state: ViewState, depth: int, lines: list[str], group_min: int
) -> None:
    pad = "  " * depth
    flag = "🔴 " if n.has_error else ""
    b = _brief(n)
    bs = f"  {b}" if b else ""
    kids = view.children(n)
    h = handle(n)
    if not kids:
        lines.append(f"{pad}- {flag}{n.kind} {n.name}{bs}")
    elif n.node_id in state.expanded:
        lines.append(f"{pad}- {flag}{n.kind} {n.name}{bs}  ⊟{h}")
        _emit_level(view, kids, state, depth + 1, lines, group_min)
    else:
        _cnt, _total, errs = _subtree_stats(view, n)
        errtag = f" · 🔴{errs}" if errs else ""
        tag = f"[{len(kids)} kids · {_fmt_ms(n.duration_ms)}{errtag}]"
        lines.append(f"{pad}- {flag}{n.kind} {n.name}{bs}  ⊕{h} {tag}")


def _emit_group(view: ViewTree, members: list[Node], depth: int, lines: list[str]) -> None:
    pad = "  " * depth
    n0 = members[0]
    durs = [m.duration_ms for m in members]
    errs = sum(1 for m in members if _subtree_stats(view, m)[2])
    errtag = f" · 🔴{errs}/{len(members)}" if errs else ""
    rng = (
        _fmt_ms(durs[0]) if min(durs) == max(durs) else f"{_fmt_ms(min(durs))}–{_fmt_ms(max(durs))}"
    )
    hs = ",".join(handle(m) for m in members[:8])
    more = "…" if len(members) > 8 else ""
    lines.append(f"{pad}- {n0.kind} {n0.name} ×{len(members)}  [{rng}{errtag}]  ⊕{hs}{more}")


def _emit_level(
    view: ViewTree,
    siblings: list[Node],
    state: ViewState,
    depth: int,
    lines: list[str],
    group_min: int,
) -> None:
    """同层兄弟：同构且未展开的折叠成 ×N 一行，其余逐个渲染（保持首次出现顺序）。"""
    groups: dict[tuple, list[Node]] = {}
    for s in siblings:
        groups.setdefault(iso_sig(view, s), []).append(s)
    emitted: set[str] = set()
    for s in siblings:
        if s.node_id in emitted:
            continue
        members = groups[iso_sig(view, s)]
        for m in members:
            emitted.add(m.node_id)
        if len(members) >= group_min:
            # 同构兄弟群：已展开的拆出来单独渲染，未展开的合成一行
            unexpanded = [m for m in members if m.node_id not in state.expanded]
            for m in members:
                if m.node_id in state.expanded:
                    _emit_node(view, m, state, depth, lines, group_min)
            if len(unexpanded) >= group_min:
                _emit_group(view, unexpanded, depth, lines)
            else:
                for m in unexpanded:
                    _emit_node(view, m, state, depth, lines, group_min)
        else:
            for m in members:
                _emit_node(view, m, state, depth, lines, group_min)


def render_explore(view: Renderable, state: ViewState, *, group_min: int = 2) -> str:
    """缩略图（空展开集）/ 展开视图。header + 树 + 操作提示。"""
    vt = view.view()
    if state.focus and state.focus in vt.by_id:
        roots = [vt.by_id[state.focus]]
        scope = f" · focus={handle(vt.by_id[state.focus])}"
    else:
        roots = vt.roots
        scope = ""
    skel = sum(1 for n in view.nodes if n.kind != SERVICE_KIND)
    svc = len(view.nodes) - skel
    lines = [
        f"trace {view.trace_id} · {skel} nodes (+{svc} svc) / {view.span_count} spans"
        f" · expanded={len(state.expanded)}{scope}",
        "",
    ]
    _emit_level(vt, roots, state, 0, lines, group_min)
    lines += [
        "",
        "⊕<handle> 折叠可展开 · ⊟ 已展开 · ×N 同构兄弟折叠；"
        "expand/collapse/focus <handle|kind:..|slowest|topN|cost>30s> · find <q> 定位",
    ]
    return "\n".join(lines)
