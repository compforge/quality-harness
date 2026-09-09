from eval_harness.model.evalset import EvalSet
from eval_harness.model.experiment import Arm, Experiment, Service
from eval_harness.model.sample import MetricResult
from eval_harness.tests.eval_cases import make_eval_case
from eval_harness.worksheet.checkpoint import from_jsonl, to_jsonl
from eval_harness.worksheet.worksheet import CellState, Worksheet


def _exp():
    return Experiment(
        name="exp",
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
        arms=[
            Arm(id="model-alpha"),
            Arm(id="model-beta", overrides={"config.tenant_id": "t2"}),
        ],
        metrics=["correctness", "citation"],
        weights={"correctness": 1.0},
        heavy_fields=["config.tenant_id"],
    )


def test_build_rows_and_provisions():
    ws = Worksheet.build(_exp())
    assert len(ws.rows) == 4  # 2 arms × 2 cases
    assert len(ws.provisions) == 2  # model-alpha(t1) and model-beta(t2) differ by heavy key
    assert ws.stats()["rows"] == 4


def test_to_sample_carries_evidence_sources():
    # gold evidence flows case → row → Sample so retrieval-recall metrics can read it
    exp = Experiment(
        name="exp",
        service=Service(name="chat", config={"tenant_id": "t1"}),
        evalsets=[
            EvalSet(
                caseset="rag",
                cases=[make_eval_case(id="q1", query="q1", evidence_sources=["d1.md", "d2.md"])],
            )
        ],
        metrics=["correctness"],
    )
    ws = Worksheet.build(exp)
    sample = next(iter(ws.rows.values())).to_sample()
    assert sample.evidence_sources == ("d1.md", "d2.md")


def test_dependency_gating():
    ws = Worksheet.build(_exp())
    # nothing solvable until provisioned
    assert ws.rows_needing_solve() == []
    assert ws.provisions_pending()
    # provision both keys
    for p in list(ws.provisions.values()):
        ws.set_provision_ok(p.key, subject_id=f"nb-{p.key}")
    assert len(ws.rows_needing_solve()) == 4
    # no scores until solve done
    assert ws.scores_needing_fill() == []
    # solve one row
    row = ws.rows[("model-alpha", "rag", "q1")]
    row.solve.state = CellState.OK
    row.solve.response = "answer"
    pending = ws.scores_needing_fill()
    assert sorted(name for r, name in pending if r is row) == [
        "citation",
        "correctness",
    ]


def test_mutation_and_completion():
    ws = Worksheet.build(_exp())
    for p in list(ws.provisions.values()):
        ws.set_provision_ok(p.key, subject_id="nb")
    for row in ws.rows.values():
        row.solve.state = CellState.OK
        for name in ws.metric_names:
            ws.set_score(row, name, MetricResult(name, "score", score=1.0))
    assert ws.is_complete()


def test_checkpoint_roundtrip(tmp_path):
    ws = Worksheet.build(_exp())
    ws.add_artifact("worksheet", "worksheet.jsonl")
    p0 = next(iter(ws.provisions.values()))
    ws.set_provision_ok(p0.key, subject_id="nb-1")
    row = ws.rows[("model-alpha", "rag", "q1")]
    row.solve.state = CellState.OK
    row.solve.response = "the answer"
    row.solve.observations = {"ttft_ms": 120, "total_ms": 3400}
    ws.set_score(
        row,
        "correctness",
        MetricResult("correctness", "score", score=0.9, judgement="good"),
    )

    path = tmp_path / "worksheet.jsonl"
    to_jsonl(ws, path)
    ws2 = from_jsonl(path)

    assert ws2.experiment == ws.experiment
    assert ws2.experiment_hash == ws.experiment_hash
    assert ws2.created_at == ws.created_at
    assert ws2.artifact_paths() == {"worksheet": "worksheet.jsonl"}
    assert len(ws2.rows) == 4
    r2 = ws2.rows[("model-alpha", "rag", "q1")]
    assert r2.solve.state is CellState.OK
    assert r2.solve.response == "the answer"
    assert r2.solve.observations["ttft_ms"] == 120
    assert r2.scores["correctness"].result.score == 0.9
    assert ws2.provisions[p0.key].subject_id == "nb-1"


def test_run_id_propagates_to_rows_and_checkpoint(tmp_path):
    ws = Worksheet.build(_exp(), run_id="run-xyz")
    assert ws.run_id == "run-xyz"
    assert all(r.run_id == "run-xyz" for r in ws.rows.values())
    to_jsonl(ws, tmp_path / "w.jsonl")
    ws2 = from_jsonl(tmp_path / "w.jsonl")
    assert ws2.run_id == "run-xyz"
    assert all(r.run_id == "run-xyz" for r in ws2.rows.values())


def test_candidate_sources_flows_case_to_row_sample_and_checkpoint(tmp_path):
    # per-case retrieval scope (CRAG-style per-query pool) flows case → row → Sample,
    # and survives a checkpoint round-trip so the solver can read it on resume.
    from eval_harness.worksheet.checkpoint import from_jsonl, to_jsonl

    exp = Experiment(
        name="exp",
        service=Service(name="chat", config={"tenant_id": "t1"}),
        evalsets=[
            EvalSet(
                caseset="rag",
                cases=[
                    make_eval_case(
                        id="q1",
                        query="q1",
                        candidate_sources=["p1.md", "p2.md", "p3.md"],
                    )
                ],
            )
        ],
        metrics=["correctness"],
    )
    ws = Worksheet.build(exp)
    row = next(iter(ws.rows.values()))
    assert row.candidate_sources == ["p1.md", "p2.md", "p3.md"]
    assert row.to_sample().candidate_sources == ("p1.md", "p2.md", "p3.md")

    ckpt = tmp_path / "ws.jsonl"
    to_jsonl(ws, ckpt)
    assert from_jsonl(ckpt).rows[("default", "rag", "q1")].candidate_sources == [
        "p1.md",
        "p2.md",
        "p3.md",
    ]
