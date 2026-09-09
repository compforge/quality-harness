"""Render pivots to markdown / csv. Pure string builders over pivot results."""

from __future__ import annotations

import csv
import io

from eval_harness.model.evalset import FacetSchema
from eval_harness.report.pivot import (
    MISSING,
    arm_summary,
    by_corpus,
    by_facet,
    compare,
    measure_metrics,
    per_case_deltas,
    quality_metrics,
    row_overall,
)
from eval_harness.worksheet.worksheet import Worksheet


def _f(x: float | None) -> str:
    return MISSING if x is None else (f"{x:.3f}".rstrip("0").rstrip("."))


def _measure_cell(m: dict[str, float] | None) -> str:
    return MISSING if not m else f"{m['mean']:.0f}(p95 {m['p95']:.0f})"


# Section captions — 每个 pivot 一句中文,说明这张表怎么读。定义在 render.py(纯模块,无
# report_kit 依赖),html.py 复用,保证 markdown 与 HTML 两份报告文案一致。反引号包字段名:
# md 原生渲染为 code,HTML 由 report_kit 的 Prose 行内渲染成 <code>。每条控制在一句。
CAP_SUMMARY = (
    "全部已评分 case 的总览。`overall` 是各质量指标的加权综合分;诊断类指标"
    "(faithfulness / coverage / retrieval_recall / citation)以 0 权重展示、不计入综合分,"
    "测量类(latency)同样不计入。"
)
CAP_BY_CORPUS = (
    "按数据集拆分——每行一个 corpus,多语料评测的首要切面。点击列头排序可快速找出最弱的数据集。"
)
CAP_RANKING = (
    "各 arm_id 按加权 `overall` 排名,获胜行高亮。带 `*` 的列为测量类指标(仅展示,不计入综合分)。"
)
CAP_PER_CASE = (
    "每个 case 在各 arm_id 下的 `overall`,按 Δ(最优 − 最差)降序排列,凸显 arm_id 间分歧最大的 case。"
    "`—` 表示该 case 在该 arm_id 未被评分(绝不按 0 计)。"
)


def cap_by_facet(facet: str) -> str:
    return (
        f"按 `{facet}` 维度分组:`n` 为该组 case 数,`overall` 为该组加权均分,各指标列为组内"
        f"均值(`—` 表示该指标对本组不适用)。"
    )


def single_report_md(
    ws: Worksheet,
    arm_id: str,
    weights: dict[str, float],
    schema: FacetSchema | None = None,
    generated_at: str | None = None,
    description: str = "",
) -> str:
    s = arm_summary(ws, arm_id, weights)
    qms, mms = quality_metrics(ws), measure_metrics(ws)
    out: list[str] = [f"# Eval Report: {ws.experiment} / arm_id={arm_id}\n"]
    if description:
        out.append(f"> {description}\n")
    if generated_at:
        out.append(f"*generated {generated_at}*\n")

    out.append("## Summary\n")
    out.append(CAP_SUMMARY + "\n")
    cov = f"{s.n_scored}/{s.n_cases} scored, {s.n_failed} failed"
    out.append(f"- overall (weighted): {_f(s.overall)}  ({cov})")
    for m in qms:
        out.append(f"- {m}: {_f(s.per_metric.get(m))}")
    for m in mms:
        out.append(f"- {m} (measure): {_measure_cell(s.per_measure.get(m))}")
    out.append("")

    # By corpus (built-in dimension): the headline cut when an experiment spans corpora.
    corpus_groups = by_corpus(ws, arm_id, weights)
    if len(corpus_groups) > 1:
        out.append("## By corpus\n")
        out.append(CAP_BY_CORPUS + "\n")
        header = ["corpus", "n", "overall"] + qms
        out.append("| " + " | ".join(header) + " |")
        out.append("| " + " | ".join(["---"] * len(header)) + " |")
        for g in corpus_groups:
            row = [g.value, str(g.n), _f(g.overall)] + [_f(g.per_metric.get(m)) for m in qms]
            out.append("| " + " | ".join(row) + " |")
        out.append("")

    # By-facet: every facet present on the arm_id's rows, uniformly (type/difficulty/… all facets).
    facets = sorted({k for r in ws.rows.values() if r.arm_id == arm_id for k in r.dimensions})
    for facet in facets:
        groups = by_facet(ws, arm_id, facet, weights, schema)
        if not groups:
            continue
        out.append(f"## By {facet}\n")
        out.append(cap_by_facet(facet) + "\n")
        header = ["value", "n", "overall"] + qms
        out.append("| " + " | ".join(header) + " |")
        out.append("| " + " | ".join(["---"] * len(header)) + " |")
        for g in groups:
            row = [g.value, str(g.n), _f(g.overall)] + [_f(g.per_metric.get(m)) for m in qms]
            out.append("| " + " | ".join(row) + " |")
        out.append("")
    return "\n".join(out)


def compare_report_md(
    ws: Worksheet,
    weights: dict[str, float],
    schema: FacetSchema | None = None,
    generated_at: str | None = None,
    description: str = "",
) -> str:
    cmp = compare(ws, weights)
    qms, mms = quality_metrics(ws), measure_metrics(ws)
    out: list[str] = [f"# Comparison: {ws.experiment}\n"]
    if description:
        out.append(f"> {description}\n")
    if generated_at:
        out.append(f"*generated {generated_at}*\n")

    # Ranking
    out.append("## Ranking (by weighted overall)\n")
    out.append(CAP_RANKING + "\n")
    header = ["rank", "arm_id", "overall", "n_scored/n_cases"] + qms + [f"{m}*" for m in mms]
    out.append("| " + " | ".join(header) + " |")
    out.append("| " + " | ".join(["---"] * len(header)) + " |")
    for i, s in enumerate(cmp.summaries, 1):
        mark = " 🏆" if s.arm_id == cmp.winner else ""
        row = [str(i), s.arm_id + mark, _f(s.overall), f"{s.n_scored}/{s.n_cases}"]
        row += [_f(s.per_metric.get(m)) for m in qms]
        row += [_measure_cell(s.per_measure.get(m)) for m in mms]
        out.append("| " + " | ".join(row) + " |")
    out.append("")
    if cmp.winner:
        line = f"**Winner: {cmp.winner}** (weights={cmp.weights})"
        if cmp.winner_caveat:
            line += f" — ⚠️ {cmp.winner_caveat}"
        out.append(line + "\n")

    # Per-case deltas (case_id union; missing = —, never 0)
    arms, table = per_case_deltas(ws, weights)
    out.append("## Per-case overall (Δ = best − worst)\n")
    out.append(CAP_PER_CASE + "\n")
    header = ["case_id"] + arms + ["Δ"]
    out.append("| " + " | ".join(header) + " |")
    out.append("| " + " | ".join(["---"] * len(header)) + " |")
    for case_id, per_arm in table.items():
        vals = [per_arm[e] for e in arms]
        present = [v for v in vals if v is not None]
        delta = _f(max(present) - min(present)) if len(present) >= 2 else MISSING
        out.append("| " + " | ".join([case_id] + [_f(v) for v in vals] + [delta]) + " |")
    out.append("")
    return "\n".join(out)


def results_csv(ws: Worksheet, weights: dict[str, float]) -> str:
    """Flat scalar projection of the table for human / spreadsheet review.

    One row per (arm_id × case) — ``arm_id`` is the comparison variant, so a reviewer
    pivots/filters by it in Excel. Columns are laid out in review order: the
    classification facets (``difficulty`` / ``type`` / …) + ``query`` + ``answer``
    (``response``) + ``ground_truth`` up front, then each quality metric as a
    ``<metric>`` score paired with a ``<metric>__judgement`` rationale (so the
    reviewer sees *why* a score was given, not just the number), then the
    ``weighted_overall`` total, measurement metrics, and provenance ids.

    Lossy on purpose: nested/list fields (retrieved chunks, citations, raw
    observations) collapse to scalars; the full structure lives in the jsonl
    checkpoint, this view is for eyeballing in a spreadsheet.
    """
    facet_keys = sorted({k for r in ws.rows.values() for k in r.dimensions})
    qms, mms = quality_metrics(ws), measure_metrics(ws)
    metric_cols: list[str] = []
    for m in qms:
        metric_cols += [m, f"{m}__judgement"]
    buf = io.StringIO()
    cols = (
        # NOTE: keep `arm_id,corpus,case_id,<facets>,query` as the contiguous leading block
        # — review tooling/tests key off this prefix.
        ["arm_id", "corpus", "case_id", *facet_keys, "query", "answer", "ground_truth"]
        + metric_cols
        + ["weighted_overall"]
        + [f"{m}" for m in mms]
        + ["n_retrieved", "message_id", "trace_id"]
    )
    w = csv.DictWriter(buf, fieldnames=cols)
    w.writeheader()
    for r in ws.rows.values():
        rec: dict = {
            "arm_id": r.arm_id,
            "corpus": r.corpus,
            "case_id": r.case_id,
            "query": r.query,
            "answer": r.solve.response,
            "ground_truth": r.ground_truth,
            "weighted_overall": row_overall(r, weights),
            "n_retrieved": len(r.solve.retrieved),
            "message_id": r.meta.get("message_id"),
            "trace_id": r.meta.get("trace_id"),
        }
        for k in facet_keys:
            rec[k] = r.dimensions.get(k)
        for m in qms:
            cell = r.scores.get(m)
            res = cell.result if cell else None
            rec[m] = res.score if res else None
            rec[f"{m}__judgement"] = res.judgement if res else None
        for m in mms:
            cell = r.scores.get(m)
            rec[m] = cell.result.value if cell and cell.result else None
        w.writerow(rec)
    return buf.getvalue()


def digest_csv(ws: Worksheet, weights: dict[str, float]) -> str:
    """Slim, eyeball-friendly projection: query + answer + each quality metric score +
    weighted_overall, one row per (arm_id × case).

    The readable middle ground between ``results.csv`` (wide: facets, ``__judgement``
    rationales, measures, provenance ids) and ``report.md`` (aggregated pivots, no
    per-case answers). Just "what was asked, what came back, how it scored" — open it,
    skim the answers, sort by a metric to find the bad ones. The full detail stays in
    results.csv / the jsonl checkpoint.
    """
    qms = quality_metrics(ws)
    buf = io.StringIO()
    cols = ["arm_id", "corpus", "case_id", "query", "answer", "weighted_overall", *qms]
    w = csv.DictWriter(buf, fieldnames=cols)
    w.writeheader()
    for r in ws.rows.values():
        rec: dict = {
            "arm_id": r.arm_id,
            "corpus": r.corpus,
            "case_id": r.case_id,
            "query": r.query,
            "answer": r.solve.response,
            "weighted_overall": row_overall(r, weights),
        }
        for m in qms:
            cell = r.scores.get(m)
            rec[m] = cell.result.score if cell and cell.result else None
        w.writerow(rec)
    return buf.getvalue()


def failures_csv(ws: Worksheet, weights: dict[str, float]) -> str:
    """Triage view: only the rows that need attention — the solve produced no answer, or
    some applicable quality metric scored 0. Carries each metric's score AND its
    ``__judgement`` (the *why*), so a reviewer jumps straight to what broke and reads the
    reason. A row that abstains (score None) is not a failure and is omitted.
    """
    qms = quality_metrics(ws)
    buf = io.StringIO()
    metric_cols: list[str] = []
    for m in qms:
        metric_cols += [m, f"{m}__judgement"]
    cols = ["arm_id", "corpus", "case_id", "query", "answer", "weighted_overall", "no_answer"]
    cols += metric_cols
    w = csv.DictWriter(buf, fieldnames=cols)
    w.writeheader()
    for r in ws.rows.values():
        results = {m: (r.scores.get(m).result if r.scores.get(m) else None) for m in qms}
        no_answer = not r.solve.response
        zero = any(res is not None and res.score == 0.0 for res in results.values())
        if not (no_answer or zero):
            continue  # passed (no answer-less row, no 0-score) → not a triage case
        rec: dict = {
            "arm_id": r.arm_id,
            "corpus": r.corpus,
            "case_id": r.case_id,
            "query": r.query,
            "answer": r.solve.response,
            "weighted_overall": row_overall(r, weights),
            "no_answer": no_answer,
        }
        for m in qms:
            res = results[m]
            rec[m] = res.score if res else None
            rec[f"{m}__judgement"] = res.judgement if res else None
        w.writerow(rec)
    return buf.getvalue()
