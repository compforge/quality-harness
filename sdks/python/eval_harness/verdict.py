"""Project an eval Worksheet into the cross-harness ``verdict.json`` (spec/verdict-schema.yaml).

The schema/serialization lives in `common.verdict`; this keeps only eval's projection.
eval scores quality (no pass/fail) — ``status`` here is *execution health* (did each cell
complete) and the quality signal rides on ``score``. ``arm_id`` is an explicit
alignment coordinate, not a case facet.
"""

from __future__ import annotations

from pathlib import Path

from harness_common import verdict as _v
from harness_common.verdict import CaseVerdict

from eval_harness.report.pivot import row_overall
from eval_harness.worksheet.worksheet import CellState, Row, Worksheet


def _row_status(row: Row) -> str:
    """Execution health of one (arm_id × case) cell: pass = solved + fully scored;
    error = a cell broke; skipped = still pending."""
    if row.solve.state is CellState.FAILED:
        return "error"
    if row.solve.state is CellState.PENDING:
        return "skipped"
    # solve OK — judge by the score cells
    states = [c.state for c in row.scores.values()]
    if any(s is CellState.FAILED for s in states):
        return "error"
    if any(s is CellState.PENDING for s in states):
        return "skipped"
    return "pass"


def _row_reason(row: Row) -> str | None:
    if row.solve.error:
        return f"solve: {row.solve.error}"[:200]
    for name, cell in row.scores.items():
        if cell.state is CellState.FAILED and cell.error:
            return f"score {name}: {cell.error}"[:200]
    return None


def _build(ws: Worksheet, weights: dict[str, float]) -> _v.RunVerdict:
    cases: list[CaseVerdict] = []
    overalls: list[float] = []
    for r in ws.rows.values():
        score = row_overall(r, weights)
        if score is not None:
            overalls.append(score)
        # one row per (Arm × case); both coordinates remain explicit on the wire.
        cases.append(
            CaseVerdict(
                case_id=r.case_id,
                arm_id=r.arm_id,
                status=_row_status(r),
                reason=_row_reason(r),
                score=round(score, 4) if score is not None else None,
                facets=dict(r.dimensions),
            )
        )
    return _v.build_run_verdict(
        "eval",
        ws.experiment,
        ws.run_id,
        cases,
        score=round(sum(overalls) / len(overalls), 4) if overalls else None,
        artifact_paths={
            "results": "results.csv",
            "worksheet": "worksheet.jsonl",
            "report": "report.md",
        },
        created_at=ws.created_at or None,
    )


def build_verdict_doc(ws: Worksheet, weights: dict[str, float]) -> dict:
    return _build(ws, weights).to_dict()


def write_verdict(run_dir: str | Path, ws: Worksheet, weights: dict[str, float]) -> Path:
    """Write ``verdict.json`` into ``run_dir``. Returns its path."""
    return _v.write_verdict(run_dir, _build(ws, weights))
