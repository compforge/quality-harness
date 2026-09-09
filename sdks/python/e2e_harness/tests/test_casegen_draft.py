"""casegen DraftCompiler (v1) — self-contained fillable draft: NL intent inlined in ``desc``,
``input`` / ``judge.e2e.assert`` left empty for a human / claude to fill. An unfilled draft (e2e
face declared, no asserts) → ``error`` in the engine, so it can NOT be hidden green by a sibling pass.
"""

from __future__ import annotations

import textwrap
from pathlib import Path

import yaml

from spec_case.model import load_caseset, validate
from e2e_harness.casegen.contract import Case as NLCase
from e2e_harness.casegen.discover import DiscoverConfig, DiscoveredCase
from e2e_harness.casegen import DraftCompiler, compile_cases
from e2e_harness.engine import run_case
from e2e_harness.runner.base import BaseRunner, Outcome, Request


class _NeverRunner(BaseRunner):
    def trigger(
        self, request: Request
    ) -> Outcome:  # pragma: no cover - must not be reached
        raise AssertionError("an empty-assert draft must error before firing")


def _dc(**kw) -> DiscoveredCase:
    nl = NLCase(
        id=kw.get("id", "dup_name"),
        desc=kw.get("desc", "重名应 409"),
        input=kw.get("input", "POST /nb {name}"),
        expect=kw.get("expect", "HTTP 409"),
        forbid=kw.get("forbid", ""),
        endpoint="create_note",
        source_module="m",
    )
    return DiscoveredCase(
        case=nl,
        handler_qualname="m.create_note",
        spec_text=kw.get("spec", "note create contract"),
        spec_id=kw.get("spec_id"),
        symbol_id="m.py::create_note",
        case_hash="h1",
        target_script_path=Path("x"),
    )


def test_draft_inlines_nl_into_desc_and_leaves_input_assert_empty():
    c = DraftCompiler().compile(_dc())
    assert c.id == "dup_name"
    assert c.input == {} and c.judge == {"e2e": {"assert": []}}  # fillable placeholders
    assert c.binding.symbol_id == "m.py::create_note"
    for needle in (
        "重名应 409",  # the NL desc
        "input(NL): POST /nb {name}",
        "expect: HTTP 409",
        "spec: note create contract",
    ):
        assert needle in c.desc  # NL intent inlined as fill-guidance


def test_unfilled_draft_errors_in_engine_not_silently_skipped():
    c = DraftCompiler().compile(_dc())
    v = run_case(c, _NeverRunner())  # errors before firing → runner never reached
    assert (
        v.status == "error" and "draft not filled" in v.reason
    )  # can't be hidden green


def test_compile_writes_draft_guidance_into_valid_case_yaml(tmp_path):
    src = tmp_path / "server"
    (src / "v1").mkdir(parents=True)
    (src / "v1" / "nb.py").write_text(
        textwrap.dedent("""
        from e2e_harness.casegen.contract import case, spec

        @spec("create contract")
        @case("happy", "合法创建", expect="HTTP 201")
        async def create(): ...
    """)
    )
    out = tmp_path / "cases.yaml"
    compile_cases(
        DiscoverConfig(source_root=src, test_root=src),
        DraftCompiler(),
        out,
        caseset="nb",
    )
    validate(load_caseset(out))  # draft file is a valid case set
    happy = next(
        c for c in yaml.safe_load(out.read_text())["cases"] if c["id"] == "happy"
    )
    assert "expect: HTTP 201" in happy["desc"] and happy["judge"]["e2e"]["assert"] == []
    assert happy["binding"] == {
        "symbol_id": "v1/nb.py::create",
        "spec": "create contract",
    }
