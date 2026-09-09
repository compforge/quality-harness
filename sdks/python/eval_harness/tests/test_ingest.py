"""Canonical case serialization (write path) + ingest helpers."""

from __future__ import annotations

import yaml
from spec_case.model import case_from_raw, case_to_raw

from eval_harness.ingest import dump_cases_yaml, slug
from eval_harness.tests.eval_cases import make_eval_case


def test_case_to_raw_omits_empty_and_none():
    c = make_eval_case(id="q1", query="hello", ground_truth="hi")
    assert case_to_raw(c) == {
        "id": "q1",
        "input": {"query": "hello"},
        "judge": {"eval": {"ground_truth": "hi"}},
    }
    assert "facets" not in case_to_raw(c)  # empty sections dropped (no [] / {} noise)


def test_refuse_serializes_without_ground_truth():
    c = make_eval_case(id="r1", query="unanswerable?", expected_behavior="refuse")
    eval_judge = case_to_raw(c)["judge"]["eval"]
    assert eval_judge == {"expected_behavior": "refuse"}  # contract honored at write time


def test_dump_cases_yaml_round_trips():
    cases = [
        make_eval_case(
            id="a",
            query="多跳问题？",  # unicode preserved
            ground_truth="ans",
            dimensions={"type": "multi_doc", "difficulty": "hard"},
            evidence_sources=["doc1.md", "doc2.md"],
        ),
        make_eval_case(
            id="b",
            query="no answer?",
            expected_behavior="refuse",
            dimensions={"type": "refuse"},
        ),
    ]
    text = dump_cases_yaml(cases)
    assert "多跳问题" in text  # allow_unicode
    reloaded = [case_from_raw(c) for c in yaml.safe_load(text)["cases"]]
    assert reloaded == cases  # full round-trip equality


def test_slug_unique_and_safe():
    a = slug("Boeing's FY2022 10-K!")
    assert a.replace("-", "").isalnum() and a.islower()
    # same base, different title → distinct (hash suffix differs)
    assert slug("Same Base") != slug("Same Base ")
    # all-symbol title still yields a stable, non-empty slug
    assert slug("***")
