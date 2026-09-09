"""Cohort —— 跨 trace 分析的承载对象；single-trace = 它的 N=1 特例。

唯一的管线 Select → Acquire → Model → Analyze → Render 有两个前端，下游共享：

    Cohort.of(trace_id, source)      取一条 trace 全量 → 一个 TraceContext → 看调用栈
    Cohort.select(query, source)     按条件查命中 span → 多 trace → facts 表 → 找共同点

「转不转 node、转多少」不是用户旋钮，是漏斗阶段（Fidelity）：
- Tier-1（默认）：只把**命中 span**（按 trace 分组的 hit-set）丢进 assemble——便宜、广。
  facts 来自 primary（model/in_chars/duration），缺卫星派生列（http_status）。
- Tier-2：`promote()` 后对可疑 trace 取全量、重 assemble——带卫星、带父子边、可 probe。

assemble 是 per-trace 的（融合靠同 trace 父子关系），所以 select 的命中 span 先按 trace_id
分组，再逐组 assemble；TraceContext 是瞬态建模单元，durable 产物是三表。
"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Callable
from pathlib import Path

from trace_harness.analyze.diagnose import diagnose
from trace_harness.corpus.tables import CorpusTables, build_tables
from trace_harness.ingest.load import build_context_from_spans
from trace_harness.ingest.sources.base import Fidelity, Source, SpanQuery
from trace_harness.ingest.sources.jaeger_file import JaegerFileSource
from trace_harness.kinds import genai
from trace_harness.model.context import TraceContext
from trace_harness.model.node import Finding, Node
from trace_harness.model.spec import SpecSet

NodePredicate = Callable[[Node], bool]


def _pred(
    kind: str | None, has_error: bool | None, predicate: NodePredicate | None
) -> NodePredicate:
    def f(n: Node) -> bool:
        if kind is not None and n.kind != kind:
            return False
        if has_error is not None and n.has_error != has_error:
            return False
        return predicate(n) if predicate else True

    return f


class Cohort:
    """一批 trace 切片 + 它们的建模/分析。内部 ctxs 瞬态，对外暴露 facts 表与算子。"""

    def __init__(
        self,
        contexts: list[TraceContext],
        *,
        source: Source | None = None,
        specset: SpecSet | None = None,
        node_filter: NodePredicate | None = None,
    ):
        self._contexts = contexts
        self._source = source
        self._specset = specset or genai.specs()
        self._filter = node_filter
        self._tables: CorpusTables | None = None

    # ——— 入口：single（of）———
    @classmethod
    def of(
        cls,
        trace_id: str,
        source: Source,
        specset: SpecSet | None = None,
        fidelity: Fidelity = Fidelity.LIGHT,
    ) -> Cohort:
        """取一条 trace 全量 → 一个 ctx。看调用栈的入口（最便宜，零判读）。"""
        spans = source.fetch(trace_id, fidelity)
        ctx = build_context_from_spans(spans, specset)
        return cls([ctx], source=source, specset=specset)

    @classmethod
    def of_file(cls, path, specset: SpecSet | None = None) -> Cohort:
        """离线文件入口（一文件一 trace）。"""
        src = JaegerFileSource(path)
        contexts = [build_context_from_spans(src.fetch(tid), specset) for tid in src._by_trace]
        return cls(contexts, source=src, specset=specset)

    # ——— 入口：cohort（select）———
    @classmethod
    def select(
        cls,
        query: SpanQuery,
        source: Source,
        specset: SpecSet | None = None,
        *,
        tier: int = 1,
    ) -> Cohort:
        """按条件查命中 span → 按 trace 分组 → 逐组 assemble。tier=1 只 hit-set，tier=2 取全量。"""
        hits = source.select(query)
        by_trace: dict[str, dict] = defaultdict(dict)
        for s in hits:
            by_trace[str(s.raw.get("traceID", "?"))][s.span_id] = s
        contexts: list[TraceContext] = []
        for tid, hit_spans in by_trace.items():
            spans = hit_spans if tier == 1 else source.fetch(tid, Fidelity.LIGHT)
            ctx = build_context_from_spans(spans, specset)
            ctx.trace_id = tid  # 单 span hit-set 也确保 trace_id 正确
            contexts.append(ctx)
        return cls(contexts, source=source, specset=specset)

    # ——— 细筛（建模后，node 级谓词）———
    def where(
        self,
        *,
        kind: str | None = None,
        has_error: bool | None = None,
        predicate: NodePredicate | None = None,
    ) -> Cohort:
        """node 级过滤（如 kind="model-call" 把传播副本收敛到源头）。返回新 Cohort。"""
        prev = self._filter
        new = _pred(kind, has_error, predicate)

        def combined(n: Node) -> bool:
            return (prev is None or prev(n)) and new(n)

        return Cohort(
            self._contexts, source=self._source, specset=self._specset, node_filter=combined
        )

    # ——— 建模产物 ———
    @property
    def contexts(self) -> list[TraceContext]:
        return self._contexts

    def _filtered(self, ctx: TraceContext) -> list[Node]:
        return [n for n in ctx.nodes if self._filter(n)] if self._filter else ctx.nodes

    def tables(self, diagnose_nodes: bool = True) -> CorpusTables:
        """三表（facts/findings/traces）。diagnose_nodes=True 跑 node 级判读填 findings。"""
        if self._tables is None:
            items = []
            for ctx in self._contexts:
                if self._filter is not None:
                    ctx = _sliced(ctx, self._filtered(ctx))
                findings = diagnose(ctx) if diagnose_nodes else {}
                items.append((ctx, findings))
            self._tables = build_tables(items)
        return self._tables

    # ——— single 渲染 ———
    def the_context(self) -> TraceContext:
        if len(self._contexts) != 1:
            raise ValueError(f"调用栈视图要求单 trace，当前 {len(self._contexts)} 条")
        return self._contexts[0]

    # ——— 跨 trace 算子 ———
    def contrast(
        self, by: tuple[str, ...] = ("kind", "name"), split: str = "has_error"
    ) -> list[dict]:
        """失败 vs 成功（或任意 split 列）逐 metric 列分布对比——证伪/证实「某 fact 是否相关」。"""
        from trace_harness.corpus.operators import contrast as _contrast

        return _contrast(self.tables(), group_by=by, split=split)

    def write(self, out_dir: str | Path, name: str = "cohort") -> Path:
        """落三表 + 报告。"""
        from trace_harness.corpus.report import write_report
        from trace_harness.corpus.store import write_tables

        out = Path(out_dir)
        out.mkdir(parents=True, exist_ok=True)
        t = self.tables()
        write_tables(t, out)
        return write_report(name, t, out)


def _sliced(ctx: TraceContext, nodes: list[Node]) -> TraceContext:
    """node 过滤后的 ctx 浅切片（保留 spans/specs，换 nodes，丢旧 view 缓存）。"""
    return TraceContext(
        trace_id=ctx.trace_id,
        spans=ctx.spans,
        nodes=nodes,
        specs=ctx.specs,
        evidence_dir=ctx.evidence_dir,
    )


# Finding 仅为类型可见性 re-export（cohort 级判读用 scope="cohort"）
__all__ = ["Cohort", "Finding"]
