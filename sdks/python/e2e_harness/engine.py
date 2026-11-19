"""0→1 e2e engine — run a structured ``common.Case`` and emit a ``CaseVerdict`` for the e2e face.

The whole e2e face as one data-driven sentence::

    CaseRun → OperationRun → protocol Runner → Outcome
    → normalize the Outcome into a response view → run case.judge["e2e"]["assert"]
    → CaseVerdict.

No scaffold, no per-case test class: the case data carries both the stimulus (``input``) and
the judgment (``judge.e2e.assert``); this engine executes them generically over the reusable
``runner.Runner`` adapter. Per-case status:

- **skipped** — no ``e2e`` face on the case (its judgment belongs to eval/perf); e2e doesn't fire it.
- **error** — an ``e2e`` face is declared but has no ``assert`` (an unfilled casegen draft), OR the
  runner couldn't fire. Either way the run is untrustworthy → ``error`` wins the rollup, so an
  unfilled draft can NOT be hidden green by a sibling ``pass``.
- **pass / fail** — fired and every / not-every assertion held.
"""

from __future__ import annotations

import re
import time
from urllib.parse import quote
from dataclasses import dataclass

from harness_common import ExperimentRun, HttpOperation, Operation, OperationRun
from spec_case.model import Case
from harness_common.execution.verdict import RunVerdict, build_run_verdict
from harness_common.execution.verdict import CaseVerdict
from e2e_harness.assertion import Assertion, run_asserts
from e2e_harness.caserun import (
    Budgets,
    CaseRef,
    CasePlan,
    CaseRun,
    Fail,
    run_lifecycle,
)
from e2e_harness.core.config import Experiment, Service
from e2e_harness.matrix import Variant
from e2e_harness.runner.base import BaseRunner, Outcome, Request


DEFAULT_BUDGETS = Budgets(prepare_s=30, execute_s=120, judge_s=30, cleanup_s=120)


@dataclass
class E2ERun(ExperimentRun):
    """One E2E ExperimentRun composed of CaseRun Executions."""

    service: str
    verdict: RunVerdict


def _execute_case(
    case: Case,
    runner: BaseRunner,
    *,
    service: Service,
    caseset: str,
    budgets: Budgets,
    invocation: tuple[HttpOperation, Request] | None,
) -> CaseRun:
    """Execute one Case into the common Execution hierarchy."""
    judge_spec = case.judge or {}
    if "e2e" not in judge_spec:
        return CaseRun(
            id=case.id,
            ref=CaseRef(caseset, case.id),
            variant=Variant(),
            status="skipped",
            phases=(),
            facets=dict(case.facets),
            reason="no e2e face",
        )

    raw_asserts = (judge_spec.get("e2e") or {}).get("assert") or []
    if not raw_asserts:
        return CaseRun(
            id=case.id,
            ref=CaseRef(caseset, case.id),
            variant=Variant(),
            status="error",
            phases=(),
            facets=dict(case.facets),
            reason="judge.e2e declared but no assert — draft not filled",
        )

    state: dict[str, Outcome] = {}
    operation_runs: list[OperationRun[Outcome]] = []

    def execute(_, run_state: dict[str, Outcome]) -> None:
        assert invocation is not None
        operation, request = invocation
        outcome = runner.trigger(request)
        operation_runs.append(
            OperationRun(
                id=f"{case.id}:{len(operation_runs)}",
                service=service,
                operation=operation,
                outcome=outcome,
            )
        )
        run_state["outcome"] = outcome

    def judge(_, run_state: dict[str, Outcome]) -> None:
        results = run_asserts(
            [Assertion.from_dict(assertion) for assertion in raw_asserts],
            response_view(run_state["outcome"]),
        )
        failed = [result for result in results if not result.ok]
        if failed:
            raise Fail("; ".join(result.detail for result in failed[:3]))

    return run_lifecycle(
        CaseRef(caseset=caseset, id=case.id),
        state,
        CasePlan(
            execute=execute,
            judge=judge,
            budgets=budgets,
            facets=case.facets,
        ),
        operation_runs=operation_runs,
    )


def response_view(outcome: Outcome) -> dict:
    """Normalize an ``Outcome`` into the flat dict that assertion paths address.

    Paths are uniform over this view: ``status`` (the code), ``body.<…>``, ``headers.<…>``,
    ``events[].<…>`` + ``event_count`` (SSE frames, from ``Outcome.metadata``), ``duration_ms``.
    """
    events = outcome.metadata.get("events", [])
    return {
        "status": outcome.status_code,
        "body": outcome.body,
        "headers": outcome.headers,
        "events": events,
        "event_count": outcome.metadata.get("event_count", len(events)),
        "duration_ms": outcome.duration_ms,
    }


def _catalog(service: Service) -> dict[str, Operation]:
    operations = {}
    for operation in service.operations:
        if operation.service_name != service.name or operation.name in operations:
            raise ValueError(
                f"Invalid Operation owner or duplicate name: {operation.id()}"
            )
        operations[operation.name] = operation
    return operations


def _bind(
    case: Case, operations: dict[str, Operation]
) -> tuple[HttpOperation, Request] | None:
    """@spec Resolve every runnable Case against declared operations before sending the batch."""
    if not ((case.judge or {}).get("e2e") or {}).get("assert"):
        return None
    inp = case.input or {}
    name = inp.get("operation")
    if not isinstance(name, str) or not name:
        raise ValueError(
            f"Case {case.id}: input.operation must name a declared HTTP Operation"
        )
    operation = operations.get(name)
    if not isinstance(operation, HttpOperation):
        raise ValueError(f"Case {case.id}: unknown HTTP Operation {name!r}")
    if "method" in inp or "path" in inp:
        raise ValueError(
            f"Case {case.id}: method and path belong to Operation {name!r}"
        )
    params = inp.get("path_params", {})
    slots = re.findall(r"\{([^{}]+)\}", operation.path)
    if not isinstance(params, dict) or set(params) != set(slots):
        raise ValueError(f"Case {case.id}: path_params must match {operation.path!r}")

    def segment(match: re.Match[str]) -> str:
        value = params[match[1]]
        if not isinstance(value, (str, int)) or str(value) in ("", ".", ".."):
            raise ValueError(f"Case {case.id}: invalid path parameter {match[1]!r}")
        return quote(str(value), safe="")

    return operation, Request(
        method=operation.method,
        path=re.sub(r"\{([^{}]+)\}", segment, operation.path),
        body=inp.get("body"),
        headers={k: str(v) for k, v in (inp.get("headers") or {}).items()},
        query={k: str(v) for k, v in (inp.get("query") or {}).items()},
    )


def run_case(
    case: Case,
    runner: BaseRunner,
    *,
    caseset: str = "cases",
    budgets: Budgets = DEFAULT_BUDGETS,
    service: Service,
) -> CaseVerdict:
    """Run one Case and project its Execution to the public CaseVerdict."""
    case_run = _execute_case(
        case,
        runner,
        service=service,
        invocation=_bind(case, _catalog(service)),
        caseset=caseset,
        budgets=budgets,
    )
    return case_run.case_verdict()


def run_cases(
    cases: list[Case],
    runner: BaseRunner,
    *,
    scope: str,
    run_id: str,
    caseset: str | None = None,
    service: Service,
) -> RunVerdict:
    """Run every case → one e2e ``RunVerdict`` (status = rollup over the per-case verdicts)."""
    experiment = Experiment(
        name=scope,
        service=service,
        caseset=caseset or scope,
    )
    return run_experiment(experiment, cases, runner, run_id=run_id).verdict


def run_experiment(
    experiment: Experiment,
    cases: list[Case],
    runner: BaseRunner,
    *,
    run_id: str,
) -> E2ERun:
    """Execute one E2E Experiment into the common ExperimentRun hierarchy."""
    operations = _catalog(experiment.service)
    invocations = [_bind(case, operations) for case in cases]
    case_runs = [
        _execute_case(
            case,
            runner,
            invocation=invocation,
            service=experiment.service,
            caseset=experiment.caseset,
            budgets=DEFAULT_BUDGETS,
        )
        for case, invocation in zip(cases, invocations, strict=True)
    ]
    verdicts = [case_run.case_verdict() for case_run in case_runs]
    verdict = build_run_verdict("e2e", experiment.name, run_id, verdicts)
    return E2ERun(
        run_id=run_id,
        experiment=experiment.name,
        created_at=time.strftime("%Y-%m-%dT%H:%M:%S"),
        executions=case_runs,
        service=experiment.service.name,
        verdict=verdict,
    )
