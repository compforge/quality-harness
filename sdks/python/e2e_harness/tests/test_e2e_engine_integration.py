"""End-to-end proof of 判定即数据: a structured case.yaml → load → engine → a REAL runner
(JSONRunner) over a httpx MockTransport SUT → Verdict.

This exercises the full path with the real runner/HTTP cycle (only the network is faked), so
it shows the design works without any hand-written test body or live service: the example
``case.yaml`` IS the contract, and the engine executes it.
"""

from __future__ import annotations

import json
from pathlib import Path

import httpx

from spec_case.model import load_caseset, validate
from e2e_harness.core.config import E2EConfig, Experiment, Service
from e2e_harness.engine import run_case, run_cases, run_experiment
from e2e_harness.runner.json_runner import JSONRunner

_CASES_YAML = Path(__file__).parent.parent / "examples" / "note_cases.yaml"


def _sut(request: httpx.Request) -> httpx.Response:
    """A tiny fake note SUT: POST /note needs a name, else 400 with an error code."""
    if request.method == "POST" and request.url.path == "/note":
        body = json.loads(request.content or b"{}")
        if not body.get("name"):
            return httpx.Response(
                400, json={"error": "name required", "code": "InvalidArgument"}
            )
        return httpx.Response(201, json={"id": "nb_123", "name": body["name"]})
    return httpx.Response(404, json={})


def _runner() -> JSONRunner:
    client = httpx.Client(transport=httpx.MockTransport(_sut), base_url="http://sut")
    return JSONRunner(
        E2EConfig(), client=client
    )  # E2EConfig() defaults: no auth, no base_url override


def test_example_caseset_loads_and_all_pass_against_the_sut():
    cs = load_caseset(_CASES_YAML)
    validate(cs)  # the committed example is a valid case set
    assert {c.id for c in cs.cases} == {"create_happy", "create_missing_name"}

    # both the happy path and the negative (error-code) path are judged purely from case data
    rv = run_cases(cs.cases, _runner(), scope="note", run_id="demo")
    assert rv.harness == "e2e"
    assert rv.status == "pass"
    assert rv.summary["total"] == 2 and rv.summary["pass"] == 2


def test_engine_catches_a_real_defect():
    # same SUT, but a case whose contract is wrong (expects 200 where the SUT returns 400)
    cs = load_caseset(_CASES_YAML)
    bad = next(c for c in cs.cases if c.id == "create_missing_name")
    bad.judge["e2e"]["assert"][0] = {"path": "status", "op": "eq", "value": 200}

    v = run_case(bad, _runner())
    assert v.status == "fail" and "got 400" in v.reason  # the engine flags the mismatch


def test_experiment_run_retains_case_operation_and_outcome() -> None:
    caseset = load_caseset(_CASES_YAML)
    service = Service(name="note")
    run = run_experiment(
        Experiment(name="note", service=service, caseset=caseset.caseset),
        [caseset.cases[0]],
        _runner(),
        run_id="evidence",
    )

    assert len(run.executions) == 1
    case_run = run.executions[0]
    assert case_run.id == caseset.cases[0].id
    assert len(case_run.operation_runs) == 1
    operation_run = case_run.operation_runs[0]
    assert operation_run.service is service
    assert operation_run.operation.method == "POST"
    assert operation_run.outcome.status_code == 201
