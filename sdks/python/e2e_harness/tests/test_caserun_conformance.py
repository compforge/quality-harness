from __future__ import annotations

from pathlib import Path

import yaml

from e2e_harness.caserun import (
    Budgets,
    CaseRef,
    CasePlan,
    Fail,
    Skip,
    run_lifecycle,
)
from e2e_harness.matrix import Variant
from harness_common.verdict import CaseVerdict, build_run_verdict


FIXTURE = Path(__file__).parents[4] / "conformance" / "e2e" / "caserun.yaml"
BUDGETS = Budgets(prepare_s=1, execute_s=1, judge_s=1, cleanup_s=1)


def _fixture() -> dict:
    return yaml.safe_load(FIXTURE.read_text(encoding="utf-8"))


def _step(action: str | None):
    if action is None:
        return None

    def run(_, __) -> None:
        if action == "pass":
            return
        if action == "error":
            raise RuntimeError("fixture error")
        if action == "fail":
            raise Fail("fixture mismatch")
        if action == "skip":
            raise Skip("fixture unavailable")
        raise RuntimeError(f"unknown fixture action: {action}")

    return run


def test_lifecycle_matches_cross_language_conformance() -> None:
    for item in _fixture()["cases"]:
        steps = item["steps"]
        result = run_lifecycle(
            CaseRef("e2e-conformance", item["id"]),
            {},
            CasePlan(
                prepare=_step(steps.get("prepare")),
                execute=_step(steps.get("execute")),
                judge=_step(steps.get("judge")),
                cleanup=_step(steps.get("cleanup")),
                budgets=BUDGETS,
                facets=item.get("facets", {}),
            ),
            variant=Variant(item.get("variant", {})),
        )
        want = item["want"]
        assert result.status == want["status"], item["id"]
        assert [phase.name for phase in result.phases] == want["phases"], item["id"]
        phase_status = {phase.name: phase.status for phase in result.phases}
        for phase, status in want.get("phase_status", {}).items():
            assert phase_status[phase] == status, item["id"]
        verdict = result.case_verdict()
        assert (verdict.arm_id or "") == want.get("arm_id", ""), item["id"]
        assert verdict.facets == want.get("facets", {}), item["id"]


def test_verdict_rollup_matches_cross_language_conformance() -> None:
    for item in _fixture()["rollups"]:
        cases = [
            CaseVerdict(case_id=item["id"], arm_id=str(index), status=status)
            for index, status in enumerate(item["statuses"])
        ]
        verdict = build_run_verdict("e2e", "e2e-conformance", item["id"], cases)
        assert verdict.status == item["want"], item["id"]
