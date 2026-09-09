"""Build the neutral report IR from a Worksheet, then render HTML.

The HTML twin of ``render.py``'s ``*_report_md`` — same pivots, same numbers, but
emitted as ``report_kit`` Tables (click-sortable, score-heat colored, winner-
highlighted) instead of markdown. eval reports carry no timeseries, so they have no
Chart → the HTML renders fully offline (no CDN). Keep this in lockstep with
``render.py``: same pivots in, same shape out.
"""

from __future__ import annotations

from harness_common.report_kit import KV, Prose, Report, Section, Table, render_html

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
)
from eval_harness.report.render import (
    CAP_BY_CORPUS,
    CAP_PER_CASE,
    CAP_RANKING,
    CAP_SUMMARY,
    cap_by_facet,
)
from eval_harness.worksheet.worksheet import Worksheet


def _f(x: float | None) -> str:
    return MISSING if x is None else (f"{x:.3f}".rstrip("0").rstrip("."))


def _measure_cell(m: dict[str, float] | None) -> str:
    return MISSING if not m else f"{m['mean']:.0f}(p95 {m['p95']:.0f})"


def _overall_tip(qms: list[str], weights: dict[str, float]) -> str:
    """The ``overall`` column's tooltip = this run's *concrete* weighting formula, not a generic
    blurb. Lists the actually-weighted metrics with their weights (``correctness×0.7 + …``);
    weight-0 (diagnostic) / measure metrics are noted as excluded."""
    parts = [f"{m}×{weights[m]:g}" for m in qms if weights.get(m, 0.0) > 0]
    if not parts:
        return "加权综合分（本次未配置非零权重）"
    return (
        "加权综合分 = "
        + " + ".join(parts)
        + "，按本行实际适用的指标归一化（不适用的退出、其余重新归一）；"
        + "权重 0 的诊断类与测量类不计入"
    )


def _col_tips(
    qms: list[str], mms: list[str], weights: dict[str, float], descriptions: dict[str, str]
) -> dict[str, str]:
    """{column label → hover tooltip} for a pivot table: the structural columns (``overall`` gets
    this run's concrete weighting formula, ``n`` a fixed blurb) plus every metric that carries a
    DESCRIPTION. Measures are labelled ``<name>*`` in the ranking table, so map both forms; labels
    with no matching column are simply never looked up by the renderer.
    """
    tips = {"overall": _overall_tip(qms, weights), "n": "该分组包含的 case 数"}
    for m in qms:
        if descriptions.get(m):
            tips[m] = descriptions[m]
    for m in mms:
        if descriptions.get(m):
            tips[m] = tips[f"{m}*"] = descriptions[m]
    return tips


def single_report_doc(
    ws: Worksheet,
    arm_id: str,
    weights: dict[str, float],
    schema: FacetSchema | None = None,
    generated_at: str | None = None,
    description: str = "",
    metric_descriptions: dict[str, str] | None = None,
) -> Report:
    s = arm_summary(ws, arm_id, weights)
    qms, mms = quality_metrics(ws), measure_metrics(ws)
    tips = _col_tips(qms, mms, weights, metric_descriptions or {})
    meta = [("experiment", ws.experiment), ("arm_id", arm_id)]
    if description:
        meta.append(("description", description))
    if generated_at:
        meta.append(("generated", generated_at))
    report = Report(title=f"Eval Report: {ws.experiment} / arm_id={arm_id}", meta=meta)

    cov = f"{s.n_scored}/{s.n_cases} scored, {s.n_failed} failed"
    summary = KV(
        [("overall (weighted)", f"{_f(s.overall)}  ({cov})")]
        + [(m, _f(s.per_metric.get(m))) for m in qms]
        + [(f"{m} (measure)", _measure_cell(s.per_measure.get(m))) for m in mms]
    )
    report.sections.append(Section("Summary", [Prose(CAP_SUMMARY), summary]))

    # By corpus — the headline cut when an experiment spans corpora.
    corpus_groups = by_corpus(ws, arm_id, weights)
    if len(corpus_groups) > 1:
        header = ["corpus", "n", "overall"] + qms
        rows = [
            [g.value, str(g.n), _f(g.overall)] + [_f(g.per_metric.get(m)) for m in qms]
            for g in corpus_groups
        ]
        report.sections.append(
            Section(
                "By corpus",
                [
                    Prose(CAP_BY_CORPUS),
                    Table(header, rows, heat_cols=list(range(2, len(header))), col_tips=tips),
                ],
            )
        )

    # By every facet present on the arm_id's rows (type / difficulty / …).
    facets = sorted({k for r in ws.rows.values() if r.arm_id == arm_id for k in r.dimensions})
    for facet in facets:
        groups = by_facet(ws, arm_id, facet, weights, schema)
        if not groups:
            continue
        header = ["value", "n", "overall"] + qms
        rows = [
            [g.value, str(g.n), _f(g.overall)] + [_f(g.per_metric.get(m)) for m in qms]
            for g in groups
        ]
        report.sections.append(
            Section(
                f"By {facet}",
                [
                    Prose(cap_by_facet(facet)),
                    Table(header, rows, heat_cols=list(range(2, len(header))), col_tips=tips),
                ],
            )
        )
    return report


def compare_report_doc(
    ws: Worksheet,
    weights: dict[str, float],
    schema: FacetSchema | None = None,
    generated_at: str | None = None,
    description: str = "",
    metric_descriptions: dict[str, str] | None = None,
) -> Report:
    cmp = compare(ws, weights)
    qms, mms = quality_metrics(ws), measure_metrics(ws)
    tips = _col_tips(qms, mms, weights, metric_descriptions or {})
    meta = [("experiment", ws.experiment)]
    if description:
        meta.append(("description", description))
    if generated_at:
        meta.append(("generated", generated_at))
    report = Report(title=f"Comparison: {ws.experiment}", meta=meta)

    # Ranking — overall + each metric; winner row highlighted, score columns heat-colored.
    header = ["rank", "arm_id", "overall", "n_scored/n_cases"] + qms + [f"{m}*" for m in mms]
    rows: list[list[str]] = []
    highlight: dict[int, str] = {}
    for i, s in enumerate(cmp.summaries):
        if s.arm_id == cmp.winner:
            highlight[i] = "winner"
        row = [str(i + 1), s.arm_id, _f(s.overall), f"{s.n_scored}/{s.n_cases}"]
        row += [_f(s.per_metric.get(m)) for m in qms]
        row += [_measure_cell(s.per_measure.get(m)) for m in mms]
        rows.append(row)
    heat = [2] + list(range(4, 4 + len(qms)))  # overall + quality metrics are in [0,1]
    ranking = Section(
        "Ranking (by weighted overall)",
        [
            Prose(CAP_RANKING),
            Table(header, rows, highlight=highlight, heat_cols=heat, col_tips=tips),
        ],
    )
    if cmp.winner:
        line = f"Winner: {cmp.winner} (weights={cmp.weights})"
        if cmp.winner_caveat:
            line += f" — ⚠️ {cmp.winner_caveat}"
        ranking.blocks.append(Prose(line))
    report.sections.append(ranking)

    # Per-case overall (Δ = best − worst); sort by Δ desc to surface the biggest gaps.
    arms, table = per_case_deltas(ws, weights)
    header = ["case_id"] + arms + ["Δ"]
    rows = []
    for case_id, per_arm in table.items():
        vals = [per_arm[e] for e in arms]
        present = [v for v in vals if v is not None]
        delta = _f(max(present) - min(present)) if len(present) >= 2 else MISSING
        rows.append([case_id] + [_f(v) for v in vals] + [delta])
    report.sections.append(
        Section(
            "Per-case overall (Δ = best − worst)",
            [
                Prose(CAP_PER_CASE),
                Table(
                    header,
                    rows,
                    heat_cols=list(range(1, 1 + len(arms))),
                    sort_default=(len(header) - 1, "desc"),
                ),
            ],
        )
    )
    return report


def single_report_html(
    ws: Worksheet,
    arm_id: str,
    weights: dict[str, float],
    schema: FacetSchema | None = None,
    generated_at: str | None = None,
    description: str = "",
    metric_descriptions: dict[str, str] | None = None,
) -> str:
    return render_html(
        single_report_doc(
            ws, arm_id, weights, schema, generated_at, description, metric_descriptions
        )
    )


def compare_report_html(
    ws: Worksheet,
    weights: dict[str, float],
    schema: FacetSchema | None = None,
    generated_at: str | None = None,
    description: str = "",
    metric_descriptions: dict[str, str] | None = None,
) -> str:
    return render_html(
        compare_report_doc(ws, weights, schema, generated_at, description, metric_descriptions)
    )
