"""Dependency-free demo producers — let the whole pipeline run without a live
system under test (``cli --mock`` / examples / tests of the orchestration)."""

from __future__ import annotations

from eval_harness.model.evalset import EvalSet
from eval_harness.model.experiment import Service
from eval_harness.schedule.reconcile import Solver, SolveResult
from eval_harness.worksheet.worksheet import Row


class EchoProvisioner:
    async def prepare(self, key: str, service: Service, evalset: EvalSet) -> str:
        return f"mock-nb-{key[:6]}"

    async def clean(self, key: str, subject_id: str) -> None:
        pass


class EchoSolver(Solver):
    """Echo ground_truth for answer cases; emit a refusal for refuse cases.

    Makes exact_match=1.0 / keyword_refusal=1.0 on the sample evalset, so the
    report renders meaningfully without any model.
    """

    async def solve(self, row: Row, service: Service, subject_id: str) -> SolveResult:
        if row.expected_behavior == "refuse":
            resp = "无法从文档中找到相关信息"
        else:
            resp = row.ground_truth or ""
        return SolveResult(
            response=resp,
            retrieved=["mock-chunk"],
            observations={"total_ms": 900, "ttft_ms": 80, "tokens": 256},
            meta={"message_id": f"mock-{row.arm_id}-{row.case_id}"},
        )
