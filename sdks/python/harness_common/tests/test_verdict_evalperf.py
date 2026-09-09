"""verdict projection for eval (Worksheet) and perf (Run/SLO)."""

from __future__ import annotations

from types import SimpleNamespace as NS

from eval_harness import verdict as EV
from eval_harness.worksheet.worksheet import (
    CellState,
    Row,
    ScoreCell,
    SolveCell,
    Worksheet,
)
from perf_harness import verdict as PV

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


# ── perf ────────────────────────────────────────────────────────────────────


def _chk(metric, op, thr, observed, state, level=None, window="measurement"):
    return NS(
        assertion=NS(
            metric=metric,
            op=op,
            threshold=thr,
            window=NS(kind=window, name=None, level=level),
        ),
        observed=observed,
        state=state,
        window_id=window,
    )


def test_perf_verdict_fail():
    # perf's judged unit is the SLO check → checks[], not cases/summary. Each check is a
    # CheckVerdict carrying name/status/metric/observed.
    run = NS(
        experiment="exp",
        run_id="rid",
        created_at="t",
        passed=False,
        trials=[
            NS(
                arm=NS(id="arm"),
                phase_errors=[],
                stop=NS(early=False),
                slo=[
                    _chk("p99_ms", "lte", 100, 150, "fail"),
                    _chk("err", "lte", 0.01, 0.0, "pass"),
                ],
            )
        ],
    )
    doc = PV.build_verdict_doc(run)
    assert (
        doc["harness"] == "perf" and doc["scope"] == "exp" and doc["status"] == "fail"
    )
    assert (
        "summary" not in doc and "cases" not in doc
    )  # no per-case unit → summary omitted
    by = {c["name"]: c for c in doc["checks"]}
    assert by["p99_ms lte 100 [window=measurement]"] == {
        "name": "p99_ms lte 100 [window=measurement]",
        "status": "fail",
        "metric": "p99_ms",
        "observed": 150,
        "arm_id": "arm",
        "window_id": "measurement",
    }
    assert (
        by["err lte 0.01 [window=measurement]"]["status"] == "pass"
        and by["err lte 0.01 [window=measurement]"]["observed"] == 0.0
    )
    assert "p99_ms [measurement] lte 100 (observed 150)" in doc["reason"]
    assert doc["artifact_paths"]["run"] == "run.json"


def test_perf_verdict_skipped_check_not_pass():
    # observed=None → skipped. passed=True (engine lenient on skip), but the only check
    # never evaluated → the recorded verdict must NOT be green (else a consumer trusts
    # an unverified run). status follows the check states, not run.passed.
    run = NS(
        experiment="e",
        run_id="r",
        created_at="t",
        passed=True,
        trials=[
            NS(
                arm=NS(id="arm"),
                phase_errors=[],
                stop=NS(early=False),
                slo=[_chk("p99_ms", "lte", 100, None, "skip")],
            )
        ],
    )
    doc = PV.build_verdict_doc(run)
    assert doc["status"] == "skipped"
    chk = doc["checks"][0]
    assert chk["status"] == "skipped" and "observed" not in chk  # None observed omitted


def test_perf_verdict_no_slo_is_skipped_not_pass():
    # trials ran but no SLO declared → nothing judged → skipped, not a vacuous pass.
    run = NS(
        experiment="e",
        run_id="r",
        created_at="t",
        passed=True,
        trials=[NS(arm=NS(id="arm"), phase_errors=[], stop=NS(early=False), slo=[])],
    )
    doc = PV.build_verdict_doc(run)
    assert (
        doc["status"] == "skipped" and "checks" not in doc
    )  # no checks → list omitted


def test_perf_verdict_no_trials_skipped():
    run = NS(experiment="e", run_id="r", created_at="", passed=True, trials=[])
    doc = PV.build_verdict_doc(run)
    assert (
        doc["status"] == "skipped" and "created_at" not in doc
    )  # empty created_at omitted


def test_perf_verdict_pass():
    run = NS(
        experiment="e",
        run_id="r",
        created_at="t",
        passed=True,
        trials=[
            NS(
                arm=NS(id="arm"),
                phase_errors=[],
                stop=NS(early=False),
                slo=[_chk("p99_ms", "lte", 100, 80, "pass")],
            )
        ],
    )
    doc = PV.build_verdict_doc(run)
    assert doc["status"] == "pass" and "reason" not in doc
    assert doc["checks"][0] == {
        "name": "p99_ms lte 100 [window=measurement]",
        "status": "pass",
        "metric": "p99_ms",
        "observed": 80,
        "arm_id": "arm",
        "window_id": "measurement",
    }


def test_perf_verdict_names_cooldown_window():
    run = NS(
        experiment="e",
        run_id="r",
        created_at="t",
        passed=True,
        trials=[
            NS(
                arm=NS(id="arm"),
                phase_errors=[],
                stop=NS(early=False),
                slo=[
                    _chk(
                        "metrics.task_count.last",
                        "lte",
                        0,
                        0,
                        "pass",
                        window="cooldown",
                    )
                ],
            )
        ],
    )
    doc = PV.build_verdict_doc(run)
    assert doc["checks"][0]["name"].endswith("[window=cooldown]")


def test_perf_verdict_cooldown_skip_fails_closed():
    run = NS(
        experiment="e",
        run_id="r",
        created_at="t",
        passed=False,
        trials=[
            NS(
                arm=NS(id="arm"),
                phase_errors=[],
                stop=NS(early=False),
                slo=[
                    _chk(
                        "client.inflight.last",
                        "lte",
                        0,
                        None,
                        "skipped",
                        window="cooldown",
                    )
                ],
            )
        ],
    )
    doc = PV.build_verdict_doc(run)
    assert doc["status"] == "fail"
    assert doc["checks"][0]["status"] == "skipped"
    assert "recovery was not observed" in doc["reason"]


def test_perf_verdict_early_stop_fails_even_when_slo_passes():
    run = NS(
        experiment="e",
        run_id="r",
        created_at="t",
        passed=False,
        trials=[
            NS(
                arm=NS(id="arm"),
                phase_errors=[],
                stop=NS(
                    early=True,
                    reason="error_rate",
                    snapshot=NS(at_s=34.0, errors=7, sent=50),
                ),
                slo=[_chk("p99_ms", "lte", 100, 80, "pass")],
            )
        ],
    )
    doc = PV.build_verdict_doc(run)
    assert doc["status"] == "fail"
    assert "stopped early" in doc["reason"] and "7/50 errors" in doc["reason"]
