from pathlib import Path
import json

import pytest
import yaml

from harness_common.environment import (
    EnvironmentFacts,
    EnvironmentSnapshot,
)
from harness_toolbox.environment import parse_environment
from e2e_harness.caserun import Budgets, CasePlan, CaseRef, Fail
from e2e_harness.environment import EnvironmentState, run_in_environment

FIXTURE = yaml.safe_load(
    (
        Path(__file__).resolve().parents[4] / "conformance/e2e/environments.yaml"
    ).read_text()
)


def test_environment_evidence_persists_on_failure(tmp_path):
    from e2e_harness.engine import E2ERun
    from e2e_harness.reducer import E2EReducer
    from harness_common.verdict import RunVerdict

    state = EnvironmentState(
        EnvironmentSnapshot("k8s", "kubernetes", host_name="devbox"), None
    )
    result = run_in_environment(
        CaseRef("environment", "contract"),
        state,
        CasePlan(execute=lambda ctx, current: None, budgets=Budgets(1, 1, 1, 1)),
    )
    assert result.status == "error"
    run = E2ERun(
        run_id="failed",
        experiment="environment",
        created_at="2026-09-15",
        service="example",
        executions=[result],
        verdict=RunVerdict(
            harness="e2e", scope="environment", run_id="failed", status=result.status
        ),
    )
    artifacts = E2EReducer().reduce(run, tmp_path)
    assert "environments" in {artifact.name for artifact in artifacts}
    evidence = json.loads((tmp_path / "environments.json").read_text())
    assert evidence[0]["environment"]["host_name"] == "devbox"


def test_environment_config_conformance(tmp_path):
    from e2e_harness.core.config import load_config

    for entry in FIXTURE["environments"]:
        assert parse_environment(entry["spec"]).kind == entry["kind"]
        path = tmp_path / "config.yaml"
        path.write_text(yaml.safe_dump({"service": {"environment": entry["spec"]}}))
        assert load_config(path).service.environment == parse_environment(entry["spec"])
    for entry in FIXTURE["invalid"]:
        with pytest.raises(ValueError):
            parse_environment(entry)


@pytest.mark.parametrize("scenario", FIXTURE["scenarios"], ids=lambda s: s["name"])
def test_environment_lifecycle_conformance(scenario):
    steps = []
    state = EnvironmentState(
        EnvironmentSnapshot("devbox-k8s", "kubernetes", scenario["name"]), None
    )

    def step(name):
        def execute(ctx, current):
            steps.append(name)
            if name == "prepare":
                current.environment.target = EnvironmentFacts(
                    "target-probe", "2026-09-15T00:00:00Z", scenario["observed"]
                )
            if scenario.get("failure") == name:
                if name == "judge":
                    raise Fail("wrong product behavior")
                raise RuntimeError("cleanup failed")
            if name == "cleanup" and "cleanup_observed" in scenario:
                current.environment.target.values.update(scenario["cleanup_observed"])

        return execute

    result = run_in_environment(
        CaseRef("environment", "contract"),
        state,
        CasePlan(
            prepare=step("prepare"),
            execute=step("execute"),
            judge=step("judge"),
            cleanup=step("cleanup"),
            budgets=Budgets(1, 1, 1, 1),
        ),
        required=scenario["required"],
    )
    assert result.status == scenario["status"]
    assert steps == scenario["steps"]
    assert result.variant.values["environment"] == "devbox-k8s"
    assert result.environment.target.source == "target-probe"
    for key, value in scenario.get("cleanup_observed", {}).items():
        assert result.environment.target.values[key] == scenario["required"][key]
        assert result.cleanup_environment.target.values[key] == value
    state.environment.target.values["later"] = "mutation"
    assert "later" not in result.environment.target.values


@pytest.mark.parametrize("prepare_error", [False, True])
def test_cleanup_does_not_overwrite_prepared_conditions(prepare_error):
    state = EnvironmentState(EnvironmentSnapshot("test", "generic"), None)

    def prepare(ctx, state):
        state.environment.target = EnvironmentFacts(
            "probe", "before", {"ptrace": "denied"}
        )
        if prepare_error:
            raise RuntimeError("partial prepare")

    def cleanup(ctx, state):
        state.environment.target.values["ptrace"] = "allowed"
        state.environment.target.observed_at = "after"

    result = run_in_environment(
        CaseRef("environment", "snapshot"),
        state,
        CasePlan(
            prepare=prepare,
            execute=lambda ctx, s: None,
            cleanup=cleanup,
            budgets=Budgets(1, 1, 1, 1),
        ),
        required={"ptrace": "denied"},
    )
    assert result.status == ("error" if prepare_error else "pass")
    assert result.environment.target.values["ptrace"] == "denied"
    assert result.environment.target.observed_at == "before"
    assert result.cleanup_environment.target.values["ptrace"] == "allowed"


def test_keyboard_interrupt_still_runs_case_cleanup():
    from e2e_harness.caserun import run_lifecycle

    cleaned = []

    def execute(ctx, state):
        raise KeyboardInterrupt

    with pytest.raises(KeyboardInterrupt):
        run_lifecycle(
            CaseRef("environment", "cancel"),
            None,
            CasePlan(
                execute=execute,
                cleanup=lambda ctx, s: cleaned.append(True),
                budgets=Budgets(1, 1, 1, 1),
            ),
        )
    assert cleaned == [True]
