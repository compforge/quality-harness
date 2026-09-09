"""assemble —— fusion 七步：raw NormSpan 集 → list[Node] + 父子边（= Canopy modeled trace 建模）。

把异构物理 span 归一成逻辑事件（node）是建模动作，node 才是分析单元——错误藏在卫星
（http）span 上、ttft 在主 span 上，不 fusion 则每个下游算子都要自己重拼"这算一次调用吗"。

七步：
  1 route      每个 span 过 SpecSet.classify → 命中即 primary 候选，未命中入未分类池
  2 own        未分类 span 归属其「最近 primary 祖先」（仲裁：最近者赢）
  3 claim      各 primary 调 spec.claims 从自己名下未分类 span 里认领卫星
  4 build      primary 调 spec.build 抽 facts 列（业务字段到此为止）
  5 connect    span_id→node_id owner 表；每个 node 沿 span 父链上溯到首个异主 span → parent_node_id
  6 residue    残余聚合：未命中也未被认领的 span 按 (最近 primary 宿主, service) 聚成 service 组——
               大 trace 几千条 redis/内部残余 span 不再各自成节点把树打爆
  7 context    realized specs（出现过的 kind）+ service spec → TraceContext
"""

from __future__ import annotations

from trace_harness.feature import bake_features
from trace_harness.feature.registry import FeatureRegistry
from trace_harness.kinds.base import SERVICE_KIND, service_spec
from trace_harness.model.context import TraceContext
from trace_harness.model.node import Node
from trace_harness.model.span import NormSpan
from trace_harness.model.span import error_text as span_error_text
from trace_harness.model.spec import KindSpec, SpecSet


def assemble(
    spans: dict[str, NormSpan],
    specset: SpecSet,
    *,
    feature_registry: FeatureRegistry | None = None,
) -> TraceContext:
    # 1 route：span → 命中的 spec（None = 未分类）
    kind_of: dict[str, KindSpec | None] = {sid: specset.classify(s) for sid, s in spans.items()}
    primaries = {sid for sid, spec in kind_of.items() if spec is not None}

    def nearest_primary(sid: str) -> str | None:
        """上溯父链，返回最近的 primary 祖先 span_id（不含自身）。"""
        cur = spans[sid].parent_span_id
        while cur and cur in spans:
            if cur in primaries:
                return cur
            cur = spans[cur].parent_span_id
        return None

    # 2 own：未分类 span 归属最近 primary 祖先
    owned: dict[str, list[str]] = {}  # primary span_id → 名下未分类后代
    for sid in spans:
        if sid in primaries:
            continue
        host = nearest_primary(sid)
        if host is not None:
            owned.setdefault(host, []).append(sid)

    # 3 claim：各 primary 从名下未分类 span 认领卫星。候选集互斥——step 2 已按
    # nearest_primary 把每个未分类 span 唯一归属到一个 primary，认领无需仲裁。
    claimed_by: dict[str, list[str]] = {}  # primary span_id → 认领的卫星 span_id
    satellite_of: dict[str, str] = {}  # 卫星 span_id → primary span_id
    for psid in primaries:
        spec = kind_of[psid]
        cands = [spans[s] for s in owned.get(psid, [])]
        if not cands or spec.claims is None:
            continue
        for csid in spec.claims(spans[psid], cands):
            claimed_by.setdefault(psid, []).append(csid)
            satellite_of[csid] = psid

    # 4 build：primary → kind 节点（抽 facts）
    nodes: list[Node] = []
    owner: dict[str, str] = {}  # span_id → node_id（primary/卫星/generic 全覆盖）

    def _mk(origin: NormSpan, kind: str, sat_ids: list[str], facts: dict) -> Node:
        span_ids = [origin.span_id, *sat_ids]
        # 6 errors（内联在节点构造里）：error_span_ids = 名下（primary+卫星）出错的 span
        errs = [sid for sid in span_ids if spans[sid].has_error]
        node = Node(
            kind=kind,
            name=origin.name,
            primary_span_id=origin.span_id,
            span_ids=span_ids,
            facts={"duration_ms": round(origin.dur_ms, 3), **facts},
            start_ms=origin.start_ms,
            duration_ms=origin.dur_ms,
            service=origin.service,
            node_id=origin.span_id,
            error_span_ids=errs,
        )
        for sid in span_ids:
            owner[sid] = node.node_id
        return node

    for psid in primaries:
        spec = kind_of[psid]
        sat_ids = claimed_by.get(psid, [])
        facts = spec.build(spans[psid], [spans[s] for s in sat_ids]) if spec.build else {}
        nodes.append(_mk(spans[psid], spec.kind, sat_ids, facts))

    # 5 connect：沿 span 父链上溯到首个异主 span → 该 span 的 node 即父
    for node in nodes:
        cur = spans[node.primary_span_id].parent_span_id
        parent_node_id = None
        while cur and cur in spans:
            host = owner.get(cur)
            if host is not None and host != node.node_id:
                parent_node_id = host
                break
            cur = spans[cur].parent_span_id
        node.parent_node_id = parent_node_id

    # 6 residue：残余按 (最近 primary 宿主, service) 聚成 service 组（错误事实保留在组上）
    groups: dict[tuple[str | None, str | None], list[str]] = {}
    for sid in spans:
        if sid in primaries or sid in satellite_of:
            continue
        groups.setdefault((nearest_primary(sid), spans[sid].service), []).append(sid)
    for (host, service), members in groups.items():
        ms = [spans[m] for m in members]
        start = min(m.start_ms for m in ms)
        end = max(m.end_ms for m in ms)
        nodes.append(
            Node(
                kind=SERVICE_KIND,
                name=service or "?",
                primary_span_id=members[0],
                span_ids=members,
                facts={"count": len(members), "sum_ms": round(sum(m.dur_ms for m in ms), 1)},
                start_ms=start,
                duration_ms=round(end - start, 3),
                service=service,
                node_id=f"svc:{host or 'root'}:{service}",
                parent_node_id=owner.get(host) if host else None,
                error_span_ids=[m.span_id for m in ms if m.has_error],
            )
        )

    # 7 context：只注册实际出现的 kind 的 spec + service
    realized: dict[str, KindSpec] = {SERVICE_KIND: service_spec()}
    for spec in specset:
        realized.setdefault(spec.kind, spec)

    # 7.5 feature：树已建好，拉所有 eager Feature 烤进 node.facts（结构不变）。须在 bake 前，
    #   好让 bake 的 project(brief) 读到派生 facts（如 model-call 卷上的 http_status）。
    #   raw_fn 给将来 eager+raw 的 Feature（build 类）用；现 builtins 只读 facts/结构。
    bake_features(
        nodes,
        raw_fn=lambda sid: spans[sid].attrs if sid in spans else {},
        registry=feature_registry,
    )

    # 8 bake：per-kind 投影 + 错误原文烤进 node（投影只此一处，下游渲染器/treecli 零 kind 依赖、
    #   可脱离 ctx 从 nodes.json 渲染）。急性投影对全树是微秒级，换单投影 + 单遍历核。
    for n in nodes:
        spec = realized.get(n.kind)
        if spec is not None and spec.project is not None:
            n.brief = spec.project(n)
        if n.has_error:
            n.error_text = span_error_text(spans[n.error_anchor])

    trace_id = next(iter(spans.values())).raw.get("traceID", "?") if spans else "?"
    return TraceContext(trace_id=trace_id, spans=spans, nodes=nodes, specs=realized)
