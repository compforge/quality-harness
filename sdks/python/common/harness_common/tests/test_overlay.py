"""common.Overlay — per-experiment keyed override of a declared catalog's defaults."""

from __future__ import annotations

import pytest

from harness_common.overlay import Overlay


def test_missing_key_uses_default():
    out = Overlay().resolve(["a", "b"], default_of=lambda _: 1.0)
    assert out == {"a": 1.0, "b": 1.0}


def test_override_beats_default():
    out = Overlay({"a": 3.0}).resolve(["a", "b"], default_of=lambda _: 1.0)
    assert out == {"a": 3.0, "b": 1.0}


def test_default_is_per_id():
    # default_of sees the id, so the catalog item can declare its own default (eval DIAGNOSTIC→0)
    out = Overlay({"a": 3.0}).resolve(
        ["a", "b"], default_of=lambda i: 0.0 if i == "b" else 9.0
    )
    assert out == {"a": 3.0, "b": 0.0}


def test_unknown_key_raises_not_silently_dropped():
    with pytest.raises(ValueError, match="unknown catalog id"):
        Overlay({"ghost": 1.0}).resolve(["a"], default_of=lambda _: 1.0)


def test_error_names_both_unknown_and_known():
    with pytest.raises(ValueError) as ei:
        Overlay({"x": 1.0, "y": 2.0}).resolve(["a", "b"], default_of=lambda _: 1.0)
    msg = str(ei.value)
    assert "'x'" in msg and "'y'" in msg  # the offending keys
    assert "'a'" in msg and "'b'" in msg  # and what was actually valid
