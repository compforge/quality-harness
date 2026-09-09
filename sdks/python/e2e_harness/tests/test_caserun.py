from __future__ import annotations

import time

from e2e_harness.caserun import (
    Budgets,
    CaseRef,
    CasePlan,
    Fail,
    Skip,
    run_lifecycle,
)
from e2e_harness.matrix import Variant, expand_matrix


def _budgets(**overrides: float) -> Budgets:
    values = {"prepare_s": 1, "execute_s": 1, "judge_s": 1, "cleanup_s": 1}
    values.update(overrides)
    return Budgets(**values)


def test_full_lifecycle_passes_state_and_records_evidence():
    state: list[str] = []

    result = run_lifecycle(
        CaseRef("sandbox-runtime", "idle_gc"),
        state,
        CasePlan(
            prepare=lambda _, s: s.append("prepare"),
            execute=lambda _, s: s.append("execute"),
            judge=lambda _, s: s.append("judge"),
            cleanup=lambda _, s: s.append("cleanup"),
            budgets=_budgets(),
            facets={"runtime": "bed"},
        ),
        variant=Variant({"executor": "supervisor"}),
    )

    assert state == ["prepare", "execute", "judge", "cleanup"]
    assert result.status == "pass"
    assert [phase.name for phase in result.phases] == [
        "prepare",
        "execute",
        "judge",
        "cleanup",
    ]
    verdict = result.case_verdict()
    assert verdict.arm_id == "executor=supervisor"
    assert verdict.facets == {"runtime": "bed", "executor": "supervisor"}
    assert "cleanup_duration_ms" in verdict.metrics


def test_cleanup_runs_after_execute_error_and_wins_when_it_errors():
    state: list[str] = []

    def execute(_, __):
        raise RuntimeError("request broke")

    def cleanup(_, s):
        s.append("cleanup")
        raise RuntimeError("resource leaked")

    result = run_lifecycle(
        CaseRef("sandbox-runtime", "cleanup_error"),
        state,
        CasePlan(execute=execute, cleanup=cleanup, budgets=_budgets()),
    )

    assert state == ["cleanup"]
    assert result.status == "error"
    assert result.reason == "cleanup: RuntimeError: resource leaked"
    assert [phase.name for phase in result.phases] == ["execute", "cleanup"]


def test_judgment_failure_is_not_an_execution_error():
    def judge(_, __):
        raise Fail("carrier was not reclaimed")

    result = run_lifecycle(
        CaseRef("sandbox-runtime", "eventual_gc"),
        {},
        CasePlan(execute=lambda *_: None, judge=judge, budgets=_budgets()),
    )

    assert result.status == "fail"
    assert result.reason == "judge: carrier was not reclaimed"


def test_skip_still_cleans_up():
    state: list[str] = []

    def prepare(_, __):
        raise Skip("runtime unsupported")

    result = run_lifecycle(
        CaseRef("sandbox-runtime", "pod_only"),
        state,
        CasePlan(
            prepare=prepare,
            execute=lambda *_: None,
            cleanup=lambda _, s: s.append("cleanup"),
            budgets=_budgets(),
        ),
    )

    assert result.status == "skipped"
    assert state == ["cleanup"]


def test_returning_after_budget_is_an_error_but_cleanup_has_fresh_budget():
    state: list[str] = []

    def execute(_, __):
        time.sleep(0.01)

    result = run_lifecycle(
        CaseRef("sandbox-runtime", "timeout"),
        state,
        CasePlan(
            execute=execute,
            cleanup=lambda _, s: s.append("cleanup"),
            budgets=_budgets(execute_s=0.001),
        ),
    )

    assert result.status == "error"
    assert "TimeoutError" in (result.reason or "")
    assert state == ["cleanup"]


def test_variant_matrix_expands_in_stable_order():
    variants = expand_matrix(
        {"runtime": ["pod", "bed"], "executor": ["supervisor", "worker"]}
    )
    assert [variant.id for variant in variants] == [
        "executor=supervisor,runtime=pod",
        "executor=supervisor,runtime=bed",
        "executor=worker,runtime=pod",
        "executor=worker,runtime=bed",
    ]
