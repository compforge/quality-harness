import asyncio
from unittest.mock import AsyncMock

import pytest

from perf_harness import load_run
from perf_harness.analysis import analyze
from perf_harness.config import load_experiment
from perf_harness.drive.load import LoadPlan
from perf_harness.drive.runner import MockRunner
from perf_harness.engine import Engine, Experiment
from perf_harness.model import ResourceProfile, Service
from perf_harness.report import write_run


@pytest.mark.parametrize("manual", [False, True])
async def test_zero_injection_skips_business_lifecycle_and_preserves_windows(tmp_path, manual):
    runner = MockRunner()
    runner.setup = AsyncMock(side_effect=AssertionError("setup forbidden"))
    runner.fire = AsyncMock(side_effect=AssertionError("fire forbidden"))
    runner.cleanup = AsyncMock(side_effect=AssertionError("cleanup forbidden"))
    runner.deactivate = AsyncMock(side_effect=AssertionError("deactivate forbidden"))
    stop = asyncio.Event()
    deployer = AsyncMock()
    experiment = Experiment(
        service=Service("mock"),
        runner=runner,
        deployer=deployer,
        teardown=True,
        loads=[LoadPlan(request_rate=0, hold_s=60 if manual else 0.02)],
        resources=[ResourceProfile()],
        stop=stop,
    )
    if manual:
        asyncio.get_running_loop().call_later(0.08, stop.set)
    run = await Engine(experiment).run()
    arm = run.arm_runs[0]
    assert [phase.kind for phase in arm.phases] == ["hold"]
    assert arm.stop.reason == ("manual" if manual else "deadline")
    assert not arm.stop.early
    assert arm.full.complete and arm.full.end_s < 1
    assert arm.started_at_s is not None
    assert not arm.requests and not arm.operation_runs
    assert all(window.request is None for window in arm.windows)
    for hook in (runner.setup, runner.fire, runner.cleanup, runner.deactivate):
        hook.assert_not_called()
    deployer.deploy.assert_not_called()
    deployer.teardown.assert_not_called()
    assert not any(note.analyzer in {"capacity", "latency"} for note in analyze(run))
    paths = write_run(run, str(tmp_path))
    restored = load_run(paths["run_dir"])
    assert restored.arm_runs[0].full.request is None
    assert restored.arm_runs[0].stop.reason == arm.stop.reason


async def test_observation_config_does_not_resolve_runner_or_require_service_url(tmp_path):
    path = tmp_path / "observe.yaml"
    path.write_text("""service:
  name: api
load:
  request_rate: 0
  hold_s: 0.01
runner:
  name: deliberately-not-registered
""")
    experiment, _ = load_experiment(str(path))
    assert experiment.runner is None
    run = await Engine(experiment).run()
    assert not run.arm_runs[0].requests
