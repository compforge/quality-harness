"""trace_harness CLI。

`single`（被动模式：离线 jaeger 文件 → 调用栈 / 判读 / html / series）；
`batch <experiment.yaml>`（Dataset：批量 jaeger → 分析结果 + 统计报告，可选 diff 基线 run）；
`treecli`（有状态探索：nodes.json IR → 缩略图 + expand/focus/find，逐步展开大树）。
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from collections.abc import Awaitable
from pathlib import Path

from trace_harness import LoadConfig, TraceContributions, TraceHarness
from trace_harness.analyze.context import AnalysisContext
from trace_harness.analyze.diagnose import diagnose
from trace_harness.analyze.measure import measure
from trace_harness.corpus.experiment import run_experiment
from trace_harness.ingest.load import build_context
from trace_harness.ingest.sources.base import SpanQuery
from trace_harness.ingest.sources.jaeger_file import JaegerFileSource
from trace_harness.kinds import genai
from trace_harness.kinds.base import _fmt_ms
from trace_harness.model.analysis import dump_analysis, load_analysis
from trace_harness.model.ir import TraceView, is_nodes_file, load_view
from trace_harness.view.explore import render_explore
from trace_harness.view.interactive import render_interactive
from trace_harness.view.measurements import measurements_md
from trace_harness.view.series import render_series
from trace_harness.view.state import ViewState, handle, resolve_selector
from trace_harness.view.text import render_text


async def _cmd_single(args: argparse.Namespace) -> int:
    path = Path(args.path)
    with path.open(encoding="utf-8") as source:
        is_analysis = '"trace-harness/analysis@2"' in source.read(256)
    saved = load_analysis(path) if is_analysis else None
    if saved:
        ctx = saved.trace
    else:
        harness = TraceHarness(TraceContributions(specs=tuple(genai.specs())))
        async with harness.open(
            JaegerFileSource(path), work_dir=args.work_dir, config=LoadConfig(lazy=args.lazy)
        ) as session:
            dataset = await session.select()
            if dataset.count != 1:
                raise ValueError("single requires exactly one trace")
            async with session.tree(dataset, next(dataset.members())) as initial:
                if args.probes:
                    initial.trace.evidence_dir = path.parent / initial.trace.trace_id
                    await session.prepare_view(initial, full=True)
                saved = await session.analyze(
                    initial,
                    detectors=None if (args.diagnose or args.probes or args.html) else [],
                    probes=args.probes,
                )
                await session.prepare_view(saved, full=bool(args.html or args.probes))
                ctx = saved.trace
    if args.series:
        kind, _, metric = args.series.partition(":")
        print(render_series(ctx, kind, metric))
        return 0
    # html 默认带判读上色；text 仅在显式 --diagnose/--probes 时判读
    want_findings = args.diagnose or args.probes or bool(args.html)
    measurements = saved.measurements if saved else measure(ctx)
    findings = (
        {key: list(value) for key, value in saved.findings.items()}
        if saved
        else (
            await diagnose(ctx, probes=args.probes, measurements=measurements)
            if want_findings
            else None
        )
    )
    if args.html:
        Path(args.html).write_text(
            render_interactive(ctx, findings, measurements=measurements), encoding="utf-8"
        )
        dump_analysis(
            AnalysisContext(ctx, measurements, findings or {}),
            Path(args.html).with_suffix(".analysis.json"),
        )
        print(f"wrote {args.html}")
        return 0
    print(render_text(ctx, findings))
    print(measurements_md(ctx, measurements))
    return 0


async def _cmd_batch(args: argparse.Namespace) -> int:
    rd = await run_experiment(args.experiment, runs_dir=args.runs_dir)
    print(f"run dir: {rd}")
    print(f"report:  {rd / 'report.html'}")
    return 0


async def _cmd_cohort(args: argparse.Namespace) -> int:
    query = SpanQuery(
        attr_eq=dict(kv.split("=", 1) for kv in (args.attr or [])), error_only=args.error
    )
    harness = TraceHarness(TraceContributions(specs=tuple(genai.specs())))
    async with harness.open(
        JaegerFileSource(args.path), work_dir=args.out, config=LoadConfig(lazy=args.lazy)
    ) as session:
        dataset = await session.select(query)
        result = await session.detect(dataset)
        print(f"dataset: {dataset.path}")
        print(f"report: {result.path / 'report.html'}")
    return 0


def _load_view(path: Path):
    """nodes.json IR → TraceView（域无关，零 raw span）；非 IR 的 jaeger 快照走 build_context。"""
    if is_nodes_file(path):
        return load_view(path)
    return TraceView.from_context(build_context(path))


def _find(view, query: str) -> str:
    hits = [view.view().by_id[nid] for nid in resolve_selector(view.nodes, query)]
    if not hits:
        return f"find {query!r}: 无匹配"
    hits.sort(key=lambda n: -n.duration_ms)
    lines = [f"find {query!r}: {len(hits)} 命中（按耗时降序，top 20）"]
    for n in hits[:20]:
        err = " 🔴" if n.has_error else ""
        lines.append(f"  ⊕{handle(n):<9} {n.kind} {n.name}  {_fmt_ms(n.duration_ms)}{err}")
    return "\n".join(lines)


def _peek(view, sel: str) -> str:
    ids = resolve_selector(view.nodes, sel)
    if not ids:
        return f"peek {sel!r}: 无匹配"
    n = view.view().by_id[ids[0]]
    out = [
        f"{n.kind} {n.name}  handle={handle(n)}  cost={_fmt_ms(n.duration_ms)}",
        f"  brief: {'  '.join(f'{f.label}={f.value}' for f in n.brief) or '(无)'}",
        f"  facts: {n.facts}",
        f"  primary_span={n.primary_span_id}  spans={len(n.span_ids)}",
    ]
    if n.has_error:
        out.append(f"  🔴 error: {n.error_text}")
        out.append(f"  error_spans: {n.error_span_ids}（原文 dump-io / probe 按 span 回取）")
    return "\n".join(out)


def _cmd_treecli(args: argparse.Namespace) -> int:
    path = Path(args.path)
    view = _load_view(path)
    state_path = path.with_name(path.name + ".view.json")
    state = ViewState.load(state_path)
    verb, sels = args.verb, args.handles or []
    if verb == "find":
        print(_find(view, sels[0] if sels else ""))
        return 0
    if verb == "peek":
        print(_peek(view, sels[0] if sels else ""))
        return 0
    if verb == "reset":
        state = ViewState()
    elif verb == "expand":
        vt = view.view()
        for s in sels:
            for nid in resolve_selector(view.nodes, s):
                # 展开目标 + 其祖先链：否则深层节点（如 expand slowest）会藏在折叠父里看不见
                cur = vt.by_id.get(nid)
                while cur is not None:
                    state.expanded.add(cur.node_id)
                    pid = cur.parent_node_id
                    cur = vt.by_id.get(pid) if pid else None
    elif verb == "collapse":
        for s in sels:
            for nid in resolve_selector(view.nodes, s):
                state.expanded.discard(nid)
    elif verb == "focus":
        ids = resolve_selector(view.nodes, sels[0]) if sels else []
        state.focus = ids[0] if ids else None
    state.save(state_path)
    print(render_explore(view, state))
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="trace", description="trace/span 分析框架")
    sub = parser.add_subparsers(dest="cmd", required=True)

    p_single = sub.add_parser("single", help="单条 trace：离线 jaeger .jsonl → 调用栈")
    p_single.add_argument("path", help="ES jaeger-span 快照 .jsonl 路径")
    p_single.add_argument("--diagnose", action="store_true", help="跑判读，Finding 上色")
    p_single.add_argument(
        "--probes",
        action="store_true",
        help="判读时落盘证据原文到 evidence_dir（有副作用，默认关；隐含 --diagnose）",
    )
    p_single.add_argument("--html", metavar="OUT", help="写自包含 HTML 调用栈到文件（带判读上色）")
    p_single.add_argument(
        "--series", metavar="KIND:METRIC", help="时序视图：某 kind 的 metric 跨迭代 sparkline"
    )
    p_single.add_argument("--lazy", action=argparse.BooleanOptionalAction, default=True)
    p_single.add_argument("--work-dir", help="持久证据缓存目录；省略使用临时目录")
    p_single.set_defaults(func=_cmd_single)

    p_batch = sub.add_parser("batch", help="Dataset：experiment.yaml → 分析结果 + 统计报告")
    p_batch.add_argument("experiment", help="experiment yaml 路径")
    p_batch.add_argument("--runs-dir", default="runs", help="run 产物根目录（默认 ./runs）")
    p_batch.set_defaults(func=_cmd_batch)

    p_cohort = sub.add_parser("cohort", help="跨 trace：按条件 select → Dataset → 分析结果")
    p_cohort.add_argument("path", help="jaeger .jsonl 文件或目录（离线 Source）")
    p_cohort.add_argument(
        "--attr",
        action="append",
        metavar="K=V",
        help="nested-tag 等值过滤（可多次，如 error.type=Foo）",
    )
    p_cohort.add_argument("--error", action="store_true", help="选择包含错误 span 的 trace")
    p_cohort.add_argument("--out", default="runs/cohort", help="产物目录")
    p_cohort.add_argument("--lazy", action=argparse.BooleanOptionalAction, default=True)
    p_cohort.set_defaults(func=_cmd_cohort)

    p_tree = sub.add_parser(
        "treecli", help="有状态探索：nodes.json IR → 缩略图 + expand/focus/find/peek"
    )
    p_tree.add_argument("path", help="nodes.json（IR）或 jaeger 快照 .jsonl（自动建模）")
    p_tree.add_argument(
        "verb",
        nargs="?",
        default="view",
        choices=["view", "expand", "collapse", "focus", "find", "peek", "reset"],
        help="view=缩略图(默认) / expand / collapse / focus / find / peek / reset",
    )
    p_tree.add_argument(
        "handles",
        nargs="*",
        help="selector：handle / kind:NAME / 名字子串 / error / slowest / topN / cost>30s",
    )
    p_tree.set_defaults(func=_cmd_treecli)

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    result = args.func(args)
    return asyncio.run(result) if isinstance(result, Awaitable) else result


if __name__ == "__main__":
    sys.exit(main())
