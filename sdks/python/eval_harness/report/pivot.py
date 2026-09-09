"""Pure pivots over a Worksheet: arm_id summary, by-facet, cross-arm_id compare, csv.

All aggregation excludes missing/abstained cells (never 0). Quality metrics
roll up via ``weighted_overall``; measurement metrics via ``percentiles``;
the two never mix.
"""

from __future__ import annotations

from dataclasses import dataclass

from eval_harness.metric.aggregate import percentiles, weighted_overall
from eval_harness.model.evalset import FacetSchema
from eval_harness.worksheet.worksheet import CellState, Row, Worksheet

MISSING = "—"


def metric_kind(ws: Worksheet, name: str) -> str:
    """Infer a metric's channel from its cells ('measure' vs quality)."""
    for row in ws.rows.values():
        cell = row.scores.get(name)
        if cell and cell.result is not None:
            return cell.result.kind
    return "score"


def quality_metrics(ws: Worksheet) -> list[str]:
    return [m for m in ws.metric_names if metric_kind(ws, m) != "measure"]


def measure_metrics(ws: Worksheet) -> list[str]:
    return [m for m in ws.metric_names if metric_kind(ws, m) == "measure"]


def _arm_rows(ws: Worksheet, arm_id: str) -> list[Row]:
    return [r for r in ws.rows.values() if r.arm_id == arm_id]


def row_overall(row: Row, weights: dict[str, float]) -> float | None:
    results = {n: c.result for n, c in row.scores.items() if c.result is not None}
    return weighted_overall(results, weights)


@dataclass
class ArmSummary:
    arm_id: str
    overall: float | None  # mean of per-row weighted overall
    per_metric: dict[str, float | None]  # quality metric -> mean score
    per_measure: dict[str, dict[str, float] | None]  # measure metric -> {mean,p50,p95,n}
    n_cases: int
    n_scored: int  # rows with a non-None overall
    n_failed: int  # rows whose solve failed


def arm_summary(ws: Worksheet, arm_id: str, weights: dict[str, float]) -> ArmSummary:
    rows = _arm_rows(ws, arm_id)
    overalls = [o for o in (row_overall(r, weights) for r in rows) if o is not None]
    per_metric: dict[str, float | None] = {}
    for m in quality_metrics(ws):
        vals = [
            r.scores[m].result.score
            for r in rows
            if m in r.scores and r.scores[m].result and r.scores[m].result.score is not None
        ]
        per_metric[m] = round(sum(vals) / len(vals), 3) if vals else None
    per_measure: dict[str, dict[str, float] | None] = {}
    for m in measure_metrics(ws):
        vals = [
            r.scores[m].result.value
            for r in rows
            if m in r.scores and r.scores[m].result and r.scores[m].result.value is not None
        ]
        per_measure[m] = percentiles(vals)
    return ArmSummary(
        arm_id=arm_id,
        overall=round(sum(overalls) / len(overalls), 3) if overalls else None,
        per_metric=per_metric,
        per_measure=per_measure,
        n_cases=len(rows),
        n_scored=len(overalls),
        n_failed=sum(1 for r in rows if r.solve.state is CellState.FAILED),
    )


@dataclass
class FacetGroup:
    facet: str
    value: str
    overall: float | None
    per_metric: dict[str, float | None]
    n: int


def by_facet(
    ws: Worksheet,
    arm_id: str,
    facet: str,
    weights: dict[str, float],
    schema: FacetSchema | None = None,
) -> list[FacetGroup]:
    """Group an arm_id's rows by one facet value → overall + per-metric per group."""
    groups: dict[str, list[Row]] = {}
    for r in _arm_rows(ws, arm_id):
        if facet in r.dimensions:
            groups.setdefault(r.dimensions[facet], []).append(r)
    out: list[FacetGroup] = []
    for value, rows in groups.items():
        overalls = [o for o in (row_overall(r, weights) for r in rows) if o is not None]
        per_metric: dict[str, float | None] = {}
        for m in quality_metrics(ws):
            vals = [
                r.scores[m].result.score
                for r in rows
                if m in r.scores and r.scores[m].result and r.scores[m].result.score is not None
            ]
            per_metric[m] = round(sum(vals) / len(vals), 3) if vals else None
        out.append(
            FacetGroup(
                facet=facet,
                value=value,
                overall=round(sum(overalls) / len(overalls), 3) if overalls else None,
                per_metric=per_metric,
                n=len(rows),
            )
        )
    key = (lambda g: schema.order_key(facet, g.value)) if schema else (lambda g: (0, g.value))
    return sorted(out, key=key)


def by_corpus(ws: Worksheet, arm_id: str, weights: dict[str, float]) -> list[FacetGroup]:
    """Group an arm_id's rows by corpus → overall + per-metric. Corpus is a built-in
    dimension (an experiment may span corpora), so this is the headline cross-dataset
    cut without corpus having to be declared as a facet."""
    groups: dict[str, list[Row]] = {}
    for r in _arm_rows(ws, arm_id):
        groups.setdefault(r.corpus, []).append(r)
    out: list[FacetGroup] = []
    for corpus, rows in sorted(groups.items()):
        overalls = [o for o in (row_overall(r, weights) for r in rows) if o is not None]
        per_metric: dict[str, float | None] = {}
        for m in quality_metrics(ws):
            vals = [
                r.scores[m].result.score
                for r in rows
                if m in r.scores and r.scores[m].result and r.scores[m].result.score is not None
            ]
            per_metric[m] = round(sum(vals) / len(vals), 3) if vals else None
        out.append(
            FacetGroup(
                facet="corpus",
                value=corpus,
                overall=round(sum(overalls) / len(overalls), 3) if overalls else None,
                per_metric=per_metric,
                n=len(rows),
            )
        )
    return out


@dataclass
class Comparison:
    summaries: list[ArmSummary]  # ranked best-overall first
    winner: str | None
    winner_caveat: str | None
    weights: dict[str, float]


def compare(ws: Worksheet, weights: dict[str, float], epsilon: float = 0.02) -> Comparison:
    arms = sorted({r.arm_id for r in ws.rows.values()})
    # Rank by overall; break ties toward the more-complete arm_id (higher n_scored),
    # so a tie on fewer cases doesn't quietly win.
    summaries = sorted(
        (arm_summary(ws, e, weights) for e in arms),
        key=lambda s: (s.overall if s.overall is not None else -1.0, s.n_scored),
        reverse=True,
    )
    winner = summaries[0].arm_id if summaries and summaries[0].overall is not None else None
    caveats: list[str] = []
    if len(summaries) >= 2 and winner is not None:
        top, second = summaries[0], summaries[1]
        if second.overall is not None and abs(top.overall - second.overall) < epsilon:
            caveats.append(f"top two within ε={epsilon} ({top.overall} vs {second.overall})")
        if top.n_scored != second.n_scored:
            caveats.append(f"coverage differs (n_scored {top.n_scored} vs {second.n_scored})")
    return Comparison(
        summaries=summaries,
        winner=winner,
        winner_caveat="; ".join(caveats) or None,
        weights=weights,
    )


def per_case_deltas(
    ws: Worksheet, weights: dict[str, float]
) -> tuple[list[str], dict[str, dict[str, float | None]]]:
    """case union × arm_id → per-case weighted overall (None if missing). Never 0.

    Case identity is ``corpus/case_id`` so the union stays collision-safe across corpora.
    """
    arms = sorted({r.arm_id for r in ws.rows.values()})
    cases = sorted({(r.corpus, r.case_id) for r in ws.rows.values()})

    def cell(arm_id: str, corpus: str, cid: str) -> float | None:
        row = ws.rows.get((arm_id, corpus, cid))
        return row_overall(row, weights) if row is not None else None

    table: dict[str, dict[str, float | None]] = {}
    for corpus, cid in cases:
        table[f"{corpus}/{cid}"] = {e: cell(e, corpus, cid) for e in arms}
    return arms, table
