"""0→1 e2e engine — run a structured Case through a (fake) runner → CaseVerdict.

No scaffold, no per-case test body: the engine reads judgment off the case data and a fake
runner stands in for the SUT, so the whole "判定即数据" path is exercised without a live service.
"""

from __future__ import annotations

from spec_case.model import Case
from e2e_harness.engine import response_view, run_case, run_cases
from e2e_harness.runner.base import BaseRunner, Outcome, Request


class _FakeRunner(BaseRunner):
    """Returns a canned Outcome, or raises — lets the engine be tested without a SUT."""

    def __init__(self, outcome: Outcome | None = None, exc: Exception | None = None):
        self._outcome, self._exc = outcome, exc

    def trigger(self, request: Request) -> Outcome:
        if self._exc:
            raise self._exc
        assert self._outcome is not None
        return self._outcome


def _case(asserts=None, *, judge=None, cid="c1") -> Case:
    if judge is None:
        judge = {"e2e": {"assert": asserts}} if asserts is not None else {}
    return Case(
        id=cid, input={"path": "/x", "body": {"q": 1}}, facets={"k": "v"}, judge=judge
    )


def _ok() -> Outcome:
    return Outcome(
        status_code=200,
        body={"id": "abc"},
        headers={"content-type": "application/json"},
    )


def test_pass_carries_id_and_facets():
    v = run_case(
        _case(
            [
                {"path": "status", "op": "eq", "value": 200},
                {"path": "body.id", "op": "not_empty"},
            ]
        ),
        _FakeRunner(_ok()),
    )
    assert v.status == "pass" and v.case_id == "c1" and v.facets == {"k": "v"}


def test_fail_carries_reason():
    v = run_case(
        _case([{"path": "status", "op": "eq", "value": 404}]), _FakeRunner(_ok())
    )
    assert v.status == "fail" and "got 200" in v.reason


def test_error_when_runner_raises_is_distinct_from_fail():
    v = run_case(
        _case([{"path": "status", "op": "eq", "value": 200}]),
        _FakeRunner(exc=ConnectionError("down")),
    )
    assert v.status == "error" and "ConnectionError" in v.reason


def test_skipped_when_no_e2e_face():
    # no e2e face — the case belongs to another face (eval); e2e neither fires nor judges it
    v = run_case(
        _case(judge={"eval": {"expected_behavior": "answer"}}), _FakeRunner(_ok())
    )
    assert v.status == "skipped"


def test_empty_e2e_assert_errors_not_skipped():
    # an e2e face declared with no asserts = an unfilled draft → error (must not pass green)
    v = run_case(_case(judge={"e2e": {"assert": []}}), _FakeRunner(_ok()))
    assert v.status == "error" and "draft" in v.reason


def test_response_view_normalizes_outcome():
    view = response_view(
        Outcome(status_code=201, body={"a": 1}, metadata={"events": [{"type": "x"}]})
    )
    assert (
        view["status"] == 201
        and view["body"] == {"a": 1}
        and view["events"] == [{"type": "x"}]
    )


def test_run_cases_rolls_up_fail_over_pass():
    cases = [
        _case([{"path": "status", "op": "eq", "value": 200}], cid="ok"),
        _case([{"path": "status", "op": "eq", "value": 500}], cid="bad"),
    ]
    rv = run_cases(cases, _FakeRunner(_ok()), scope="demo", run_id="r1")
    assert rv.harness == "e2e" and rv.status == "fail"  # fail wins the rollup over pass
    assert (
        rv.summary["total"] == 2 and rv.summary["fail"] == 1 and rv.summary["pass"] == 1
    )
