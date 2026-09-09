"""Test helper: build a canonical ``common.Case`` the way eval reads it — the inverse of
``eval_harness.model.evalset.eval_view``. Keeps eval-test case construction concise after the
input side collapsed onto ``common.Case`` (no per-harness case class to instantiate).
"""

from __future__ import annotations

from spec_case.model import Case


def make_eval_case(
    id: str,
    query: str = "q",
    *,
    expected_behavior: str = "answer",
    ground_truth: str | None = None,
    dimensions: dict[str, str] | None = None,
    evidence_sources: list[str] | None = None,
    candidate_sources: list[str] | None = None,
) -> Case:
    """Assemble an eval case: ``query`` (+ optional ``candidate_sources``) → ``input``,
    scoring contract → ``judge.eval``, ``dimensions`` → ``facets``. Omits empty sections so
    the result matches a hand-written canonical case (and ``case_to_raw`` round-trips)."""
    inp: dict = {"query": query}
    if candidate_sources:
        inp["candidate_sources"] = list(candidate_sources)
    judge_eval: dict = {}
    if expected_behavior != "answer":
        judge_eval["expected_behavior"] = expected_behavior
    if ground_truth is not None:
        judge_eval["ground_truth"] = ground_truth
    if evidence_sources:
        judge_eval["evidence_sources"] = list(evidence_sources)
    return Case(
        id=id,
        input=inp,
        facets=dict(dimensions or {}),
        judge={"eval": judge_eval} if judge_eval else {},
    )
