"""``judge.e2e.assert`` — the structured, executable form of e2e judgment (判定即数据).

An assertion is a ``{path, op, value}`` triple checked against the SUT response. This is the
deterministic core of the e2e face: no LLM, no human-written test body — **the case data IS
the check**. The op set mirrors ``spec/case-schema.yaml`` (judge.e2e.assert) and the legacy
``judge.assert_judge.AssertJudge`` methods, but driven by *data* instead of method calls, so
the same checks are now reviewable, hashable (intent-drift) and cross-language.

The evaluator works over a plain **response-view dict** (the Outcome→view bridge lives in
``e2e_harness.engine``), so it is pure and unit-testable without a live runner. Paths address
that view uniformly: ``status``, ``body.id``, ``headers.content-type``, ``events[].type``
(project a field over a list), ``data.items[0].id`` (index).
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any, Literal

# op vocabulary — kept in lockstep with spec/case-schema.yaml (judge.e2e.assert.op enum).
Op = Literal["eq", "not_empty", "contains", "matches", "in", "gt", "gte", "within"]

_MISSING = object()  # path resolved to nothing — kept distinct from a present ``None``


@dataclass(frozen=True)
class Assertion:
    """One structured check: ``op`` applied to the value at ``path``, against ``value``.

    ``value`` is the comparison operand (omitted for ``not_empty``; a 2-list ``[lo, hi]`` for
    ``within``; a list for ``in``).
    """

    path: str
    op: str
    value: Any = None

    @classmethod
    def from_dict(cls, d: dict) -> "Assertion":
        return cls(path=str(d["path"]), op=str(d["op"]), value=d.get("value"))


@dataclass(frozen=True)
class AssertOutcome:
    assertion: Assertion
    ok: bool
    actual: Any
    detail: str  # human-facing why (empty when ok) — surfaced in the verdict reason


def _tokens(path: str) -> list[str]:
    """``a.b[].c`` → ``["a", "b", "[]", "c"]``;  ``x[0]`` → ``["x", "[0]"]``."""
    out: list[str] = []
    for part in path.split("."):
        m = re.match(r"^([^\[\]]*)(.*)$", part)
        key, brackets = (m.group(1), m.group(2)) if m else (part, "")
        if key:
            out.append(key)
        out.extend(re.findall(r"\[\d*\]", brackets))
    return out


def _resolve(data: Any, tokens: list[str]) -> Any:
    if not tokens:
        return data
    tok, rest = tokens[0], tokens[1:]
    if tok == "[]":  # project the remaining path over every list element
        if not isinstance(data, list):
            return _MISSING
        return [_resolve(el, rest) for el in data]
    if tok.startswith("[") and tok.endswith("]"):  # list index, e.g. "[0]"
        try:
            return _resolve(data[int(tok[1:-1])], rest)
        except (ValueError, IndexError, TypeError, KeyError):
            return _MISSING
    if isinstance(data, dict) and tok in data:  # dict key
        return _resolve(data[tok], rest)
    return _MISSING


def resolve_path(data: dict, path: str) -> Any:
    """Value at ``path`` in the response view, or ``_MISSING`` when absent."""
    return _resolve(data, _tokens(path))


def _is_empty(v: Any) -> bool:
    # ``_MISSING`` and empty containers/strings/None are empty; 0 / False are *values*.
    return v is _MISSING or v is None or v == "" or v == [] or v == {} or v == ()


def check(op: str, actual: Any, value: Any) -> tuple[bool, str]:
    """Apply one op to a resolved value; return ``(ok, short detail)``. Empty detail iff ok."""
    if op == "not_empty":
        return (not _is_empty(actual)), ("" if not _is_empty(actual) else "is empty")
    if actual is _MISSING:
        return False, "path not found"
    if op == "eq":
        ok = actual == value
        return ok, "" if ok else f"expected {value!r}, got {actual!r}"
    if op == "contains":
        try:
            ok = value in actual
        except TypeError:
            ok = False
        return ok, "" if ok else f"{actual!r} does not contain {value!r}"
    if op == "matches":
        ok = re.search(str(value), str(actual)) is not None
        return ok, "" if ok else f"{actual!r} does not match /{value}/"
    if op == "in":
        ok = isinstance(value, (list, tuple, set)) and actual in value
        return ok, "" if ok else f"{actual!r} not in {value!r}"
    if op in ("gt", "gte"):
        try:
            ok = actual > value if op == "gt" else actual >= value
        except TypeError:
            return False, f"{actual!r} not comparable to {value!r}"
        return ok, "" if ok else f"{actual!r} not {op} {value!r}"
    if op == "within":
        try:
            lo, hi = value
            ok = lo <= actual <= hi
        except (TypeError, ValueError):
            return False, f"within needs [lo, hi], got {value!r}"
        return ok, "" if ok else f"{actual!r} not within [{lo}, {hi}]"
    return False, f"unknown op {op!r}"


def evaluate(assertion: Assertion, data: dict) -> AssertOutcome:
    actual = resolve_path(data, assertion.path)
    ok, why = check(assertion.op, actual, assertion.value)
    detail = "" if ok else f"{assertion.path} {assertion.op} {assertion.value!r}: {why}"
    return AssertOutcome(
        assertion=assertion,
        ok=ok,
        actual=(None if actual is _MISSING else actual),
        detail=detail,
    )


def run_asserts(asserts: list[Assertion], data: dict) -> list[AssertOutcome]:
    """Evaluate every assertion against the response view — all, not short-circuit (so one
    run surfaces every failing check, not just the first)."""
    return [evaluate(a, data) for a in asserts]
