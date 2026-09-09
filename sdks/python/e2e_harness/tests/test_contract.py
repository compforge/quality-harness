"""Tests for e2e_harness.contract (decorators, Case, hashing, yaml loader)."""

from __future__ import annotations

import textwrap
from pathlib import Path

import pytest

from e2e_harness.casegen.contract import (
    CASE_ID_PATTERN,
    Case,
    CaseSpec,
    case,
    case_hash,
    get_cases,
    get_links,
    get_rules,
    get_spec,
    get_spec_id,
    link,
    load_cases_file,
    rule,
    spec,
)


class TestDecorators:
    def test_case_decorator_attaches(self):
        @case("happy", "minimal happy path")
        def handler(): ...

        cases = get_cases(handler)
        assert len(cases) == 1
        assert cases[0] == CaseSpec(
            id="happy", desc="minimal happy path", input="", expect="", forbid=""
        )

    def test_case_stacking_preserves_source_order(self):
        @case("first", "first case")
        @case("second", "second case")
        def handler(): ...

        cases = get_cases(handler)
        # @case nearest to fn runs first; outer @case prepends. Source-top-to-bottom:
        assert [c.id for c in cases] == ["first", "second"]

    def test_case_with_optional_fields(self):
        @case("c1", "d1", input="i1", expect="e1", forbid="f1")
        def handler(): ...

        cases = get_cases(handler)
        assert cases[0].input == "i1"
        assert cases[0].expect == "e1"
        assert cases[0].forbid == "f1"

    def test_case_rejects_invalid_id(self):
        with pytest.raises(ValueError, match="must match"):

            @case("Bad-Id", "desc")
            def handler(): ...

    def test_case_rejects_empty_desc(self):
        with pytest.raises(ValueError, match="desc"):

            @case("ok", "")
            def handler(): ...

    def test_case_rejects_duplicate_id(self):
        with pytest.raises(ValueError, match="duplicate"):

            @case("dup", "first")
            @case("dup", "second")
            def handler(): ...

    def test_spec_decorator_attaches(self):
        @spec("endpoint contract here", id="create_note")
        def handler(): ...

        assert get_spec(handler) == "endpoint contract here"
        assert get_spec_id(handler) == "create_note"

    def test_spec_strips_whitespace(self):
        @spec("\n  some text  \n")
        def handler(): ...

        assert get_spec(handler) == "some text"

    def test_spec_rejects_empty(self):
        with pytest.raises(ValueError):

            @spec("")
            def handler(): ...

    def test_spec_rejects_invalid_id(self):
        with pytest.raises(ValueError, match="must match"):

            @spec("contract", id="Bad-ID")
            def handler(): ...

    def test_link_decorator_attaches(self):
        @link("docs/tenancy.md")
        def handler(): ...

        assert get_links(handler) == ["docs/tenancy.md"]

    def test_link_stacking_preserves_source_order(self):
        @link("docs/a.md")
        @link("mod.py::Other.method")
        def handler(): ...

        assert get_links(handler) == ["docs/a.md", "mod.py::Other.method"]

    def test_link_rejects_empty(self):
        with pytest.raises(ValueError, match="link"):

            @link("  ")
            def handler(): ...

    def test_rule_decorator_attaches(self):
        @rule("watch new synchronous DB calls on this hot path")
        def handler(): ...

        assert get_rules(handler) == ["watch new synchronous DB calls on this hot path"]

    def test_rule_stacking_preserves_source_order(self):
        @rule("first rule")
        @rule("second rule")
        def handler(): ...

        assert get_rules(handler) == ["first rule", "second rule"]

    def test_rule_strips_whitespace(self):
        @rule("\n  some rule  \n")
        def handler(): ...

        assert get_rules(handler) == ["some rule"]

    def test_rule_rejects_empty(self):
        with pytest.raises(ValueError, match="rule"):

            @rule("")
            def handler(): ...

    def test_unmarked_handler_has_empty_markers(self):
        def handler(): ...

        assert get_links(handler) == []
        assert get_rules(handler) == []


class TestCaseHash:
    def _make_case(self, **overrides) -> Case:
        defaults = dict(
            id="c1",
            desc="d",
            input=None,
            expect=None,
            forbid=None,
            endpoint="ep",
            source_module="m",
            cases_file=None,
            source="decorator",
        )
        defaults.update(overrides)
        return Case(**defaults)

    def test_stable_across_runs(self):
        c = self._make_case()
        assert case_hash(c, "spec text") == case_hash(c, "spec text")

    def test_changes_when_desc_changes(self):
        c1 = self._make_case(desc="old")
        c2 = self._make_case(desc="new")
        assert case_hash(c1, "spec") != case_hash(c2, "spec")

    def test_changes_when_spec_changes(self):
        c = self._make_case()
        assert case_hash(c, "spec a") != case_hash(c, "spec b")

    def test_none_vs_empty_spec_same(self):
        c = self._make_case()
        assert case_hash(c, None) == case_hash(c, "")

    def test_hash_is_8_hex_chars(self):
        c = self._make_case()
        h = case_hash(c, "spec")
        assert len(h) == 8
        assert all(ch in "0123456789abcdef" for ch in h)


class TestYamlLoader:
    def test_single_endpoint(self, tmp_path: Path):
        path = tmp_path / "x_cases.yaml"
        path.write_text(
            textwrap.dedent("""\
            endpoint: do_something
            cases:
              - id: case_a
                desc: first case
              - id: case_b
                desc: second
                input: req body
                expect: status 200
                forbid: empty body
        """)
        )
        cases = load_cases_file(path, source_module="m.x")
        assert [c.id for c in cases] == ["case_a", "case_b"]
        assert cases[0].endpoint == "do_something"
        assert cases[1].input == "req body"
        assert cases[1].expect == "status 200"
        assert cases[1].forbid == "empty body"
        assert all(c.source == "yaml" for c in cases)

    def test_multi_endpoint(self, tmp_path: Path):
        path = tmp_path / "x_cases.yaml"
        path.write_text(
            textwrap.dedent("""\
            endpoints:
              ep_a:
                cases:
                  - id: a1
                    desc: first
              ep_b:
                cases:
                  - id: b1
                    desc: second
        """)
        )
        cases = load_cases_file(path, source_module="m.x")
        by_ep = {c.endpoint: c for c in cases}
        assert set(by_ep) == {"ep_a", "ep_b"}
        assert by_ep["ep_a"].id == "a1"
        assert by_ep["ep_b"].id == "b1"

    def test_rejects_missing_endpoint(self, tmp_path: Path):
        path = tmp_path / "bad_cases.yaml"
        path.write_text("cases:\n  - id: x\n    desc: y\n")
        with pytest.raises(ValueError, match="endpoint"):
            load_cases_file(path, source_module="m")

    def test_rejects_duplicate_id(self, tmp_path: Path):
        path = tmp_path / "x_cases.yaml"
        path.write_text(
            textwrap.dedent("""\
            endpoint: ep
            cases:
              - id: dup
                desc: first
              - id: dup
                desc: second
        """)
        )
        with pytest.raises(ValueError, match="duplicate"):
            load_cases_file(path, source_module="m")


def test_case_id_pattern():
    assert CASE_ID_PATTERN.match("good_id")
    assert CASE_ID_PATTERN.match("a1_b2")
    assert not CASE_ID_PATTERN.match("Bad")
    assert not CASE_ID_PATTERN.match("1starts_with_digit")
    assert not CASE_ID_PATTERN.match("has-dash")
