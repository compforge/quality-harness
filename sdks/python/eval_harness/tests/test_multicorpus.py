"""Multi-corpus experiment: one experiment spanning several corpora →
one worksheet, provisioned per corpus, with corpus as a built-in report dimension."""

from __future__ import annotations

from eval_harness.model.evalset import EvalSet
from eval_harness.model.experiment import Experiment, Service
from eval_harness.model.sample import MetricResult
from eval_harness.report.pivot import by_corpus
from eval_harness.report.render import digest_csv, failures_csv, results_csv
from eval_harness.tests.eval_cases import make_eval_case
from eval_harness.worksheet.worksheet import CellState, Worksheet


def _multi_exp():
    return Experiment(
        name="suite",
        service=Service(name="chat", config={"tenant_id": "t1"}),
        evalsets=[
            EvalSet(
                caseset="finance",
                cases=[make_eval_case(id="q1", query="q", ground_truth="a")],
            ),
            # same case id under a different corpus must coexist (keyed by corpus)
            EvalSet(
                caseset="news",
                cases=[make_eval_case(id="q1", query="q", ground_truth="a")],
            ),
        ],
        metrics=["correctness"],
    )


def test_single_corpus_is_one_evalset():
    exp = Experiment(
        name="e",
        service=Service(name="chat"),
        evalsets=[EvalSet(caseset="solo", cases=[make_eval_case(id="q1", query="q")])],
        metrics=["correctness"],
    )
    assert exp.corpora == ["solo"]  # a single-corpus run is just evalsets with one entry


def test_build_spans_corpora_and_provisions_each():
    ws = Worksheet.build(_multi_exp())
    assert len(ws.rows) == 2  # 1 arm_id × 2 corpora × 1 case
    assert len(ws.provisions) == 2  # one provisioned resource per corpus
    assert ("default", "finance", "q1") in ws.rows
    assert (
        "default",
        "news",
        "q1",
    ) in ws.rows  # same id, different corpus — no collision
    assert {r.corpus for r in ws.rows.values()} == {"finance", "news"}
    # the two corpora map to distinct provision keys
    assert (
        ws.rows[("default", "finance", "q1")].arm_key != ws.rows[("default", "news", "q1")].arm_key
    )


def _filled():
    ws = Worksheet.build(_multi_exp())
    for p in ws.provisions.values():
        ws.set_provision_ok(p.key, "nb")
    for r in ws.rows.values():
        r.solve.state, r.solve.response = CellState.OK, "x"
        score = 1.0 if r.corpus == "finance" else 0.0
        ws.set_score(r, "correctness", MetricResult("correctness", "score", score=score))
    return ws


def test_by_corpus_pivot():
    groups = {g.value: g.overall for g in by_corpus(_filled(), "default", {"correctness": 1.0})}
    assert groups == {"finance": 1.0, "news": 0.0}


def test_results_csv_has_corpus_column():
    csv_out = results_csv(_filled(), {"correctness": 1.0})
    assert csv_out.splitlines()[0].startswith("arm_id,corpus,case_id,")
    assert "default,finance,q1," in csv_out
    assert "default,news,q1," in csv_out


def test_results_digest_csv_is_slim():
    out = digest_csv(_filled(), {"correctness": 1.0})
    header = out.splitlines()[0]
    # slim: query + answer + metric score + overall; NO __judgement / provenance / facets
    assert header == "arm_id,corpus,case_id,query,answer,weighted_overall,correctness"
    assert "__judgement" not in out and "trace_id" not in out
    assert "default,finance,q1,q,x,1.0,1.0" in out  # query=q answer=x overall=1 correctness=1


def test_failures_csv_only_problem_rows():
    out = failures_csv(_filled(), {"correctness": 1.0})
    lines = out.splitlines()
    assert lines[0].endswith("no_answer,correctness,correctness__judgement")
    # news/q1 scored correctness 0 → included; finance/q1 scored 1 (passed) → excluded
    assert any(",news,q1," in ln for ln in lines[1:])
    assert not any(",finance,q1," in ln for ln in lines[1:])
