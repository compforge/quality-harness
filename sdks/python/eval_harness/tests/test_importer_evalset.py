"""Unified EvalSet (corpus + sources + cases): SourceRecord, dump_evalset round-trip,
Importer base, and config loading an evalset.yaml file."""

from __future__ import annotations

import pytest

from eval_harness.config import _load_evalset
from eval_harness.ingest import Importer, dump_evalset
from eval_harness.model.evalset import EvalSet, SourceRecord
from eval_harness.tests.eval_cases import make_eval_case


def _es():
    return EvalSet(
        caseset="fin",
        focus="财报",
        sources=[
            SourceRecord(name="A.md", content="alpha text"),
            SourceRecord(name="B.pdf", uri="https://x/B.pdf", meta={"resource_type": "file"}),
        ],
        cases=[make_eval_case(id="q1", query="q", ground_truth="a", evidence_sources=["A.md"])],
    )


def test_source_needs_uri_or_content():
    with pytest.raises(ValueError, match="uri.*content"):
        SourceRecord(name="x")


def test_dump_evalset_materialises_content_and_round_trips(tmp_path):
    path = dump_evalset(_es(), tmp_path)
    assert path.name == "caseset.yaml"
    # inline content written to docs/ and rewritten to a uri
    assert (tmp_path / "docs").is_dir()
    es = _load_evalset(str(path), tmp_path.parent)  # path-form entry
    assert es.caseset == "fin" and es.focus == "财报"
    assert {s.name for s in es.sources} == {"A.md", "B.pdf"}
    a = next(s for s in es.sources if s.name == "A.md")
    assert a.uri and a.uri.endswith(".txt")  # materialised file, resolved to abs path
    b = next(s for s in es.sources if s.name == "B.pdf")
    assert b.uri == "https://x/B.pdf"  # URL passed through
    assert [c.id for c in es.cases] == ["q1"]


def test_importer_build_and_write(tmp_path):
    class Demo(Importer):
        def build(self) -> EvalSet:
            return _es()

    out = Demo().write(tmp_path)
    assert out.is_file()
    assert _load_evalset(str(out), tmp_path.parent).corpus == "fin"


def test_caseset_resolves_relative_sources(tmp_path):
    (tmp_path / "d.md").write_text("hi", encoding="utf-8")
    path = tmp_path / "cases.yaml"
    path.write_text(
        "caseset: c\nsources: [{name: d.md, uri: d.md}]\n"
        "cases: [{id: q1, input: {query: q}, requires: [d.md]}]\n",
        encoding="utf-8",
    )
    es = _load_evalset(str(path), tmp_path)
    assert es.sources[0].uri == str((tmp_path / "d.md").resolve())


def test_public_api_surface():
    # the curated names are importable straight from the top package (stable API)
    import eval_harness as eh

    for n in [
        "Experiment",
        "Arm",
        "Service",
        "LLMSpec",
        "EvalSet",
        "Case",
        "SourceRecord",
        "Importer",
        "Solver",
        "Provisioner",
        "SolveResult",
        "BaseMetric",
        "LLMJudge",
        "Worksheet",
        "run_experiment",
        "load_experiment",
        "register",
        "fetch_json",
    ]:
        assert hasattr(eh, n), f"missing public name: {n}"


def test_fetch_json_caches_and_parses(tmp_path):
    from eval_harness.ingest import fetch_json

    src = tmp_path / "data.json"
    src.write_text('{"hello": [1, 2, 3]}', encoding="utf-8")
    got = fetch_json(src.as_uri(), cache_dir=tmp_path / "cache")
    assert got == {"hello": [1, 2, 3]}
    assert (tmp_path / "cache").is_dir()  # cached under the given dir
