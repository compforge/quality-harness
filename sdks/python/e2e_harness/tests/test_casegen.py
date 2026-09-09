"""casegen step ① — Compiler/StubCompiler + compile/check/list orchestration + drift gate.

Deterministic (no LLM): a fixture source with @case markers → discover → StubCompiler → a
committed case.yaml + ``compiled_from`` intent hashes. Drift, reuse-preserves-edits, and the
no-LLM ``check`` gate are all exercised.
"""

from __future__ import annotations

import textwrap
from pathlib import Path

import yaml

from spec_case.model import load_caseset, validate
from e2e_harness.casegen.discover import DiscoverConfig
from e2e_harness.casegen import (
    StubCompiler,
    check_cases,
    compile_cases,
    list_cases,
)

_HANDLER = """
from e2e_harness.casegen.contract import case, spec

@spec("note create contract")
@case("happy", "合法创建应成功", expect="HTTP 201")
@case("dup_name", "重名应 409", expect="HTTP 409")
async def create_note(): ...
"""


def _source(tmp: Path, body: str = _HANDLER) -> Path:
    src = tmp / "server"
    (src / "v1").mkdir(parents=True, exist_ok=True)
    (src / "v1" / "note.py").write_text(textwrap.dedent(body))
    return src


def _cfg(src: Path) -> DiscoverConfig:
    return DiscoverConfig(source_root=src, test_root=src)


def test_compile_writes_caseset_and_compiled_from(tmp_path):
    out = tmp_path / "cases.yaml"
    rep = compile_cases(_cfg(_source(tmp_path)), StubCompiler(), out, caseset="note")
    assert sorted(rep.compiled) == ["dup_name", "happy"] and not rep.reused

    cs = load_caseset(out)
    validate(cs)  # the written file is a valid case set the engine can load
    assert cs.caseset == "note" and {c.id for c in cs.cases} == {
        "happy",
        "dup_name",
    }
    compiled_from = yaml.safe_load(out.read_text())["compiled_from"]
    assert set(compiled_from) == {"happy", "dup_name"}  # intent hash recorded per case


def test_drift_detected_when_marker_changes(tmp_path):
    src = _source(tmp_path)
    out = tmp_path / "cases.yaml"
    compile_cases(_cfg(src), StubCompiler(), out, caseset="nb")
    assert check_cases(_cfg(src), out).ok  # in sync right after compile

    # change a marker's NL intent (expect) → its intent hash drifts
    _source(tmp_path, _HANDLER.replace('expect="HTTP 409"', 'expect="HTTP 422"'))
    rep = check_cases(_cfg(src), out)
    assert not rep.ok and rep.drifted == ["dup_name"] and not rep.orphaned


def test_reuse_preserves_filled_asserts_when_intent_unchanged(tmp_path):
    src = _source(tmp_path)
    out = tmp_path / "cases.yaml"
    compile_cases(_cfg(src), StubCompiler(), out, caseset="nb")

    # simulate a human/LLM filling the asserts for `happy`
    doc = yaml.safe_load(out.read_text())
    for c in doc["cases"]:
        if c["id"] == "happy":
            c["judge"] = {
                "e2e": {"assert": [{"path": "status", "op": "eq", "value": 201}]}
            }
    out.write_text(yaml.safe_dump(doc, sort_keys=False, allow_unicode=True))

    # re-compile with UNCHANGED markers → the filled asserts must survive (not clobbered by stub)
    rep = compile_cases(_cfg(src), StubCompiler(), out, caseset="nb")
    assert "happy" in rep.reused and not rep.compiled
    happy = next(c for c in load_caseset(out).cases if c.id == "happy")
    assert happy.judge["e2e"]["assert"] == [
        {"path": "status", "op": "eq", "value": 201}
    ]


def test_orphaned_when_marker_removed(tmp_path):
    src = _source(tmp_path)
    out = tmp_path / "cases.yaml"
    compile_cases(_cfg(src), StubCompiler(), out, caseset="nb")

    # remove the dup_name marker → it's orphaned in the compiled file
    _source(
        tmp_path,
        _HANDLER.replace('@case("dup_name", "重名应 409", expect="HTTP 409")\n', ""),
    )
    assert check_cases(_cfg(src), out).orphaned == ["dup_name"]


def test_list_reports_status(tmp_path):
    src = _source(tmp_path)
    out = tmp_path / "cases.yaml"
    compile_cases(_cfg(src), StubCompiler(), out, caseset="nb")
    assert dict(list_cases(_cfg(src), out)) == {
        "happy": "in-sync",
        "dup_name": "in-sync",
    }
