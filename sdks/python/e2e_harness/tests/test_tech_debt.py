"""Regression tests: ``Case`` inherits ``CaseSpec`` (no field duplication)."""

from __future__ import annotations

import pytest

from e2e_harness.casegen.contract import Case, CaseSpec


class TestCaseInheritance:
    def test_case_is_a_casespec(self):
        c = Case(
            id="happy",
            desc="d",
            input="i",
            expect="e",
            forbid="f",
            endpoint="ep",
            source_module="m",
            cases_file=None,
            source="decorator",
        )
        assert isinstance(c, CaseSpec)
        assert c.id == "happy" and c.input == "i"

    def test_unset_fields_default_to_empty_string(self):
        c = Case(id="c1", desc="d", endpoint="ep")
        assert c.input == ""
        assert c.expect == ""
        assert c.forbid == ""
        assert c.cases_file is None
        assert c.source == "decorator"

    def test_invalid_id_rejected(self):
        with pytest.raises(ValueError, match="invalid case id"):
            Case(id="Bad-Id", desc="d", endpoint="ep")
