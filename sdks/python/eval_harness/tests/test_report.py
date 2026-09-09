from eval_harness.model.evalset import EvalSet, FacetSchema
from eval_harness.model.experiment import Arm, Experiment, Service
from eval_harness.model.sample import MetricResult
from eval_harness.report.html import single_report_html
from eval_harness.report.pivot import by_facet, compare, per_case_deltas
from eval_harness.report.render import compare_report_md, results_csv, single_report_md
from eval_harness.tests.eval_cases import make_eval_case
from eval_harness.worksheet.worksheet import CellState, Worksheet


def _ws():
    exp = Experiment(
        name="cmp",
        service=Service(name="chat", config={"tenant_id": "t1"}),
        evalsets=[
            EvalSet(
                caseset="rag",
                cases=[
                    make_eval_case(
                        id="q1",
                        query="q1",
                        ground_truth="a1",
                        dimensions={"difficulty": "easy"},
                    ),
                    make_eval_case(
                        id="q2",
                        query="q2",
                        ground_truth="a2",
                        dimensions={"difficulty": "hard"},
                    ),
                ],
            )
        ],
        arms=[Arm(id="model-alpha"), Arm(id="model-beta")],
        metrics=["correctness", "latency"],
        weights={"correctness": 1.0},
    )
    ws = Worksheet.build(exp)
    for p in list(ws.provisions.values()):
        ws.set_provision_ok(p.key, "nb")
    data = {
        ("model-alpha", "rag", "q1"): (0.9, 1000),
        ("model-alpha", "rag", "q2"): (0.5, 2000),
        ("model-beta", "rag", "q1"): (0.7, 500),
        ("model-beta", "rag", "q2"): (0.3, 600),
    }
    for key, (corr, lat) in data.items():
        r = ws.rows[key]
        r.solve.state = CellState.OK
        r.solve.response = "ans"
        ws.set_score(r, "correctness", MetricResult("correctness", "score", score=corr))
        ws.set_score(r, "latency", MetricResult("latency", "measure", value=lat, unit="ms"))
    return ws


def test_compare_ranking_and_winner():
    ws = _ws()
    cmp = compare(ws, {"correctness": 1.0})
    assert [s.arm_id for s in cmp.summaries] == ["model-alpha", "model-beta"]
    assert cmp.winner == "model-alpha"
    assert cmp.summaries[0].overall == 0.7 and cmp.summaries[1].overall == 0.5
    # measurement aggregated separately, not in overall
    assert cmp.summaries[0].per_measure["latency"]["mean"] == 1500


def test_by_facet_ordered():
    ws = _ws()
    groups = by_facet(ws, "model-alpha", "difficulty", {"correctness": 1.0}, FacetSchema())
    assert [g.value for g in groups] == ["easy", "hard"]  # ordinal, not alpha
    assert groups[0].overall == 0.9 and groups[1].overall == 0.5


def test_missing_cell_never_zero():
    ws = _ws()
    # wipe model-beta/q2 correctness back to pending → its overall is None, dropped
    ws.rows[("model-beta", "rag", "q2")].scores["correctness"].state = CellState.PENDING
    ws.rows[("model-beta", "rag", "q2")].scores["correctness"].result = None
    cmp = compare(ws, {"correctness": 1.0})
    model_beta = next(s for s in cmp.summaries if s.arm_id == "model-beta")
    assert model_beta.overall == 0.7  # only q1, NOT (0.7+0)/2
    assert model_beta.n_scored == 1 and model_beta.n_cases == 2
    assert cmp.winner_caveat and "coverage" in cmp.winner_caveat
    # per-case table marks the missing one None, not 0
    _, table = per_case_deltas(ws, {"correctness": 1.0})
    assert table["rag/q2"]["model-beta"] is None  # case identity is corpus/case_id


def test_renderers_smoke():
    ws = _ws()
    w = {"correctness": 1.0}
    assert "Comparison: cmp" in compare_report_md(ws, w, FacetSchema())
    assert "By difficulty" in single_report_md(ws, "model-alpha", w, FacetSchema())
    csv_out = results_csv(ws, w)
    assert "arm_id,corpus,case_id,difficulty,query" in csv_out
    assert "model-alpha,rag,q1" in csv_out


def test_report_header_extras_and_captions():
    # the report carries an optional generated-at stamp + experiment description in its
    # header, and a per-section caption explaining how to read each pivot — md and html twins.
    ws = _ws()
    w = {"correctness": 1.0}
    md = single_report_md(
        ws,
        "model-alpha",
        w,
        FacetSchema(),
        generated_at="2026-06-02 21:00 CST",
        description="冒烟评测",
    )
    assert "*generated 2026-06-02 21:00 CST*" in md
    assert "> 冒烟评测" in md
    assert "按 `difficulty` 维度分组" in md  # Chinese section caption, backticks kept
    html = single_report_html(
        ws,
        "model-alpha",
        w,
        FacetSchema(),
        generated_at="2026-06-02 21:00 CST",
        description="冒烟评测",
    )
    assert "2026-06-02 21:00 CST" in html and "冒烟评测" in html
    # caption renders in html with the field name as an inline <code> span (not literal backticks)
    assert "按 <code>difficulty</code> 维度分组" in html
    assert "`difficulty`" not in html
    # both extras are optional — omitting them yields no stamp/quote line
    bare = single_report_md(ws, "model-alpha", w, FacetSchema())
    assert "*generated" not in bare and "\n> " not in bare


def test_metric_header_tooltip_in_html():
    # a metric's DESCRIPTION surfaces as an instant CSS hover tooltip on its column header — the
    # text rides in data-tip (not native title=, which has a laggy browser delay), html only.
    ws = _ws()
    html = single_report_html(
        ws,
        "model-alpha",
        {"correctness": 1.0},
        FacetSchema(),
        metric_descriptions={"correctness": "答案是否正确"},
    )
    assert '<th onclick="rkSort(this)" class="tip" data-tip="答案是否正确">correctness</th>' in html
    assert "title=" not in html  # no native title tooltip (the slow one) anywhere
    # the `overall` tooltip spells out THIS run's concrete weighting formula, not a generic blurb
    assert 'data-tip="加权综合分 = correctness×1' in html
    # no descriptions passed → no metric tooltip, but structural (overall/n) tips still render
    plain = single_report_html(ws, "model-alpha", {"correctness": 1.0}, FacetSchema())
    assert "答案是否正确" not in plain
    assert "加权综合分 = correctness×1" in plain
