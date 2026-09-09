"""Tests for e2e_harness.casegen.discover (AST scan of @case/@spec markers — casegen's front-end)."""

from __future__ import annotations

import textwrap
from pathlib import Path

import pytest

from e2e_harness.casegen.discover import DiscoverConfig, discover


# --------------------------------------------------------------------------- discover


class TestDiscover:
    def _setup_handler(self, source_root: Path, rel_dir: str, content: str) -> Path:
        # source_root is the scan dir directly (source_subdir merged away).
        full_dir = source_root / rel_dir
        full_dir.mkdir(parents=True, exist_ok=True)
        handler = full_dir / "note.py"
        handler.write_text(textwrap.dedent(content))
        return handler

    def test_discover_decorator_only(self, tmp_path: Path):
        source_root = tmp_path / "server"
        test_root = tmp_path / "tests"
        self._setup_handler(
            source_root,
            "v1/handlers",
            """
            from e2e_harness.casegen.contract import case, spec

            @spec("endpoint contract", id="create_contract")
            @case("happy_minimal", "minimal happy path")
            @case("missing_field", "missing required field")
            async def create_note(): ...
        """,
        )

        cases = discover(DiscoverConfig(source_root=source_root, test_root=test_root))
        assert len(cases) == 2
        ids = {c.case.id for c in cases}
        assert ids == {"happy_minimal", "missing_field"}

        c = next(c for c in cases if c.case.id == "happy_minimal")
        assert c.case.endpoint == "create_note"
        assert c.spec_text == "endpoint contract"
        assert c.spec_id == "create_contract"
        assert c.symbol_id == "v1/handlers/note.py::create_note"
        # group-keyed path (default group), not a mirror of the handler source dir
        expected_path = test_root / "default" / "test_create_note__happy_minimal.py"
        assert c.target_script_path == expected_path

    def test_discover_yaml_only(self, tmp_path: Path):
        source_root = tmp_path / "server"
        test_root = tmp_path / "tests"
        handler = self._setup_handler(
            source_root,
            "v1/handlers",
            """
            async def create_note(): ...
        """,
        )
        (handler.parent / "note_cases.yaml").write_text(
            textwrap.dedent("""\
            endpoint: create_note
            cases:
              - id: yaml_case
                desc: from yaml
                input: req body
        """)
        )

        cases = discover(DiscoverConfig(source_root=source_root, test_root=test_root))
        assert len(cases) == 1
        assert cases[0].case.id == "yaml_case"
        assert cases[0].case.source == "yaml"
        assert cases[0].case.input == "req body"

    def test_discover_yaml_overrides_decorator(self, tmp_path: Path):
        source_root = tmp_path / "server"
        test_root = tmp_path / "tests"
        handler = self._setup_handler(
            source_root,
            "v1/handlers",
            """
            from e2e_harness.casegen.contract import case

            @case("collide", "from decorator")
            async def create_note(): ...
        """,
        )
        (handler.parent / "note_cases.yaml").write_text(
            textwrap.dedent("""\
            endpoint: create_note
            cases:
              - id: collide
                desc: from yaml (wins)
        """)
        )

        with pytest.warns(UserWarning, match="yaml wins"):
            cases = discover(
                DiscoverConfig(source_root=source_root, test_root=test_root)
            )
        assert len(cases) == 1
        assert cases[0].case.desc == "from yaml (wins)"
        assert cases[0].case.source == "yaml"

    def test_hash_drift_when_spec_changes(self, tmp_path: Path):
        source_root = tmp_path / "server"
        test_root = tmp_path / "tests"
        handler = self._setup_handler(
            source_root,
            "h",
            """
            from e2e_harness.casegen.contract import case, spec

            @spec("v1 contract")
            @case("c1", "the case")
            async def h_fn(): ...
        """,
        )
        cases_v1 = discover(
            DiscoverConfig(source_root=source_root, test_root=test_root)
        )

        handler.write_text(
            textwrap.dedent("""
            from e2e_harness.casegen.contract import case, spec

            @spec("v2 contract changed")
            @case("c1", "the case")
            async def h_fn(): ...
        """)
        )
        cases_v2 = discover(
            DiscoverConfig(source_root=source_root, test_root=test_root)
        )
        assert cases_v1[0].case_hash != cases_v2[0].case_hash

    def test_plural_named_specs_on_same_symbol_are_preserved(self, tmp_path: Path):
        source_root = tmp_path / "server"
        test_root = tmp_path / "tests"
        self._setup_handler(
            source_root,
            "h",
            """
            from e2e_harness.casegen.contract import case, spec

            @spec("string contract", id="string_input")
            @case("string_happy", "string path")
            def parse(): ...

            @spec("integer contract", id="integer_input")
            @case("integer_happy", "integer path")
            def parse(): ...
        """,
        )

        cases = discover(DiscoverConfig(source_root=source_root, test_root=test_root))
        assert [(item.case.id, item.spec_id) for item in cases] == [
            ("string_happy", "string_input"),
            ("integer_happy", "integer_input"),
        ]

    def test_target_path_keyed_by_group(self, tmp_path: Path):
        source_root = tmp_path / "server"
        test_root = tmp_path / "tests"
        self._setup_handler(
            source_root,
            "rpc/v1/handlers",
            """
            from e2e_harness.casegen.contract import case

            @case("c1", "d1")                       # default group
            async def do_thing(): ...

            @case("c2", "d2", group="widgets")      # explicit group
            async def make_widget(): ...
        """,
        )
        cases = discover(DiscoverConfig(source_root=source_root, test_root=test_root))
        by_id = {c.case.id: c for c in cases}
        # path keyed by group, NOT mirroring the handler source dir
        assert (
            by_id["c1"].target_script_path == test_root / "default/test_do_thing__c1.py"
        )
        assert (
            by_id["c2"].target_script_path
            == test_root / "widgets/test_make_widget__c2.py"
        )

    def test_same_endpoint_same_group_collides(self, tmp_path: Path):
        """Same endpoint name under two surfaces, same (default) group → collision."""
        source_root = tmp_path / "server"
        test_root = tmp_path / "tests"
        for sub in ("rpc/v1/handlers", "rest/v1/endpoints"):
            self._setup_handler(
                source_root,
                sub,
                """
                from e2e_harness.casegen.contract import case

                @case("happy", "d")
                async def create_note(): ...
            """,
            )
        with pytest.raises(ValueError, match="collision"):
            discover(DiscoverConfig(source_root=source_root, test_root=test_root))


# --------------------------------------------------------------------------- scripts
