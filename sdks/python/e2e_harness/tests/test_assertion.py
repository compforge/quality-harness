"""judge.e2e.assert evaluator — the data-driven structured-assertion core (判定即数据)."""

from __future__ import annotations

from e2e_harness.assertion import Assertion, check, evaluate, resolve_path, run_asserts

_VIEW = {
    "status": 200,
    "body": {
        "id": "abc",
        "items": [{"name": "a"}, {"name": "b"}],
        "zero": 0,
        "blank": "",
    },
    "headers": {"content-type": "application/json"},
    "events": [{"type": "start"}, {"type": "delta"}, {"type": "done"}],
}


def test_path_dotted_index_and_projection():
    assert resolve_path(_VIEW, "status") == 200
    assert resolve_path(_VIEW, "body.id") == "abc"
    assert resolve_path(_VIEW, "body.items[0].name") == "a"
    assert resolve_path(_VIEW, "events[].type") == [
        "start",
        "delta",
        "done",
    ]  # project over list
    assert resolve_path(_VIEW, "headers.content-type") == "application/json"


def test_eq_and_missing_path():
    assert evaluate(Assertion("status", "eq", 200), _VIEW).ok
    bad = evaluate(Assertion("status", "eq", 404), _VIEW)
    assert not bad.ok and "got 200" in bad.detail
    miss = evaluate(Assertion("body.nope", "eq", 1), _VIEW)  # absent → fail, not crash
    assert not miss.ok and "path not found" in miss.detail


def test_not_empty_zero_is_value_blank_and_absent_are_empty():
    assert evaluate(Assertion("body.id", "not_empty"), _VIEW).ok
    assert evaluate(
        Assertion("body.zero", "not_empty"), _VIEW
    ).ok  # 0 is a value, not empty
    assert not evaluate(Assertion("body.blank", "not_empty"), _VIEW).ok  # "" is empty
    assert not evaluate(
        Assertion("body.absent", "not_empty"), _VIEW
    ).ok  # missing is empty


def test_op_semantics():
    assert check("contains", "abc", "b")[0]
    assert check("contains", ["start", "done"], "done")[0]
    assert check("matches", "abc123", r"\d+")[0]
    assert check("in", "done", ["start", "done"])[0]
    assert not check("in", "x", ["start", "done"])[0]
    assert check("gt", 3, 2)[0] and check("gte", 3, 3)[0]
    assert check("within", 3, [1, 5])[0] and not check("within", 9, [1, 5])[0]


def test_events_projection_contains():
    # SSE: assert the stream carried a 'done' frame, via events[].type contains
    assert evaluate(Assertion("events[].type", "contains", "done"), _VIEW).ok


def test_run_asserts_reports_every_failure_not_just_first():
    results = run_asserts(
        [Assertion("status", "eq", 200), Assertion("body.id", "eq", "WRONG")], _VIEW
    )
    assert results[0].ok and not results[1].ok


def test_unknown_op_fails_clean():
    ok, why = check("regex", "x", "y")
    assert not ok and "unknown op" in why
