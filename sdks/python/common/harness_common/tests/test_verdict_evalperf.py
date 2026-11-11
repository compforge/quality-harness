"""verdict projection for eval (Worksheet)."""

from __future__ import annotations


from eval_harness import verdict as EV
from eval_harness.worksheet.worksheet import (
    CellState,
    Row,
    ScoreCell,
    SolveCell,
    Worksheet,
)

OK, FAILED, PENDING = CellState.OK, CellState.FAILED, CellState.PENDING


# ── eval ────────────────────────────────────────────────────────────────────


def _row(case_id, solve_state, score_states, dims=None, err=None):
    r = Row(
        arm_id="model-alpha",
        arm_key="model-alpha",
        corpus="c",
        case_id=case_id,
        query="q",
    )
    r.solve = SolveCell(
        state=solve_state, response="a" if solve_state is OK else None, error=err
    )
    r.scores = {
        name: ScoreCell(state=st, error=("x" if st is FAILED else None))
        for name, st in score_states.items()
    }
    r.dimensions = dims or {}
    return r


def test_eval_verdict():
    ws = Worksheet("exp", "hash", ["m"], run_id="rid")
    ws.rows = {
        ("model-alpha", "c", "ok"): _row("ok", OK, {"m": OK}, {"type": "factual"}),
        ("model-alpha", "c", "bad"): _row(
            "bad", FAILED, {}, err="boom"
        ),  # solve broke → error
        ("model-alpha", "c", "pend"): _row(
            "pend", OK, {"m": PENDING}
        ),  # not fully scored → skipped
    }
    doc = EV.build_verdict_doc(ws, weights={})

    assert doc["harness"] == "eval" and doc["scope"] == "exp" and doc["run_id"] == "rid"
    assert doc["status"] == "error"  # error precedence over pass/skipped
    assert (
        "summary" not in doc
    )  # counts are read-time-derived from cases[], not on the wire
    by = {c["case_id"]: c for c in doc["cases"]}
    assert by["ok"]["status"] == "pass"
    assert by["ok"]["arm_id"] == "model-alpha"
    assert by["ok"]["facets"] == {"type": "factual"}
    assert by["bad"]["status"] == "error" and by["bad"]["reason"].startswith(
        "solve: boom"
    )
    assert "1 error" in doc["reason"]


def test_eval_verdict_all_pass_omits_reason():
    ws = Worksheet("exp", "hash", ["m"], run_id="rid")
    ws.rows = {("model-alpha", "c", "ok"): _row("ok", OK, {"m": OK})}
    doc = EV.build_verdict_doc(ws, weights={})
    assert doc["status"] == "pass" and "reason" not in doc
