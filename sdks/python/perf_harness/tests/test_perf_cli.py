"""CLI execution health is independent of request success codes."""

import asyncio

import pytest

from perf_harness import cli
from perf_harness.drive.load import LoadPlan, Warmup
from perf_harness.drive.runner import Runner
from perf_harness.engine import Experiment
from perf_harness.model import Outcome, ResourceProfile, Service
from perf_harness.runio import load_run


@pytest.mark.parametrize(
    "http_status,setup_error,abort_rate,expected_status,expected_exit,expected_code",
    [
        (200, False, None, "completed", 0, "success"),
        (503, False, None, "completed", 0, "http_503"),
        (503, False, 0.5, "stopped", 2, "http_503"),
        (200, True, None, "error", 1, None),
    ],
    ids=["completed", "completed-with-request-errors", "breaker-stopped", "setup-error"],
)
def test_run_exit_code_describes_execution(
    tmp_path,
    monkeypatch,
    http_status,
    setup_error,
    abort_rate,
    expected_status,
    expected_exit,
    expected_code,
):
    class TestRunner(Runner):
        async def setup(self, ctx):
            if setup_error:
                raise RuntimeError("target unavailable")

        async def fire(self, ctx):
            await asyncio.sleep(0.001)
            return Outcome(status=http_status, duration_ms=1.0)

    experiment = Experiment(
        name="cli-execution",
        service=Service("chat", base_url="http://127.0.0.1:0"),
        runner=TestRunner(),
        resources=[ResourceProfile()],
        loads=[
            LoadPlan(
                warmup=Warmup(step_s=0),
                request_rate=float("inf"),
                max_inflight=1,
                hold_s=0.1,
                abort_on_error_rate=abort_rate,
                breaker_min_n=1,
            )
        ],
        probes=[],
    )
    # Keep the real Engine and artifact writer; only replace configuration assembly.
    monkeypatch.setattr(cli, "load_experiment", lambda path, mock: (experiment, str(tmp_path)))
    config = tmp_path / "config.yaml"
    config.write_text("name: cli-execution\n")
    assert cli.main(["run", str(config), "--run-id", "test"]) == expected_exit

    run = load_run(tmp_path / "cli-execution" / "test")
    assert run.status == expected_status
    codes = {evaluation.code for arm in run.arm_runs for evaluation in arm.evaluations.values()}
    assert codes == ({expected_code} if expected_code else set())
