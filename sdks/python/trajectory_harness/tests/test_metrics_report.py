from __future__ import annotations

from datetime import datetime, timezone

from trajectory_harness import (
    ExecutionResult,
    ExecutionSuccessVerifier,
    Failure,
    Metric,
    TrajectoryAnalysisRun,
    aggregate_metrics,
    verify,
    render_report_html,
)
from trajectory_harness.model import (
    make_atif_step as Step,
    make_atif_trajectory as Trajectory,
)


def _run(run_id: str, day: int, success: bool) -> TrajectoryAnalysisRun:
    failure = None
    if not success:
        failure = Failure(
            kind="llm",
            phase="request",
            error_type="timeout",
            code="APITimeoutError",
        )
    trajectory = Trajectory(
        trajectory_id=f"trajectory-{run_id}",
        steps=(
            Step(
                step_id=f"step-{run_id}",
                parent_step_id=None,
                operation="chat",
                name="model",
                start_ms=0,
                duration_ms=10,
                status="error" if failure else "ok",
                failure=failure,
            ),
        ),
        execution=ExecutionResult(
            outcome="completed" if success else "timeout",
            duration_ms=10 if success else 30,
            failure=failure,
        ),
    )
    verifier = ExecutionSuccessVerifier()
    return TrajectoryAnalysisRun(
        run_id=run_id,
        created_at=datetime(2026, 8, day, tzinfo=timezone.utc),
        dataset_id="reviews",
        dataset_version="",
        trajectory_ids=(trajectory.trajectory_id,),
        trajectory_targets=((trajectory.trajectory_id, "review1"),),
        verifications=(
            verify(
                trajectory,
                [verifier],
                target="review1",
                category="effect",
            ),
        ),
        verifier_specs=(verifier.spec,),
        annotation_count=1,
    )


def test_aggregate_metrics_combines_execution_failure_and_verification():
    metrics = aggregate_metrics(_run("run-2", 2, False))
    by_name = {metric.qualified_name: metric.value for metric in metrics}

    assert by_name["execution.timeout.rate{target=review1}"] == 1
    assert (
        by_name[
            "verification.pass.rate{target=review1,category=effect,"
            "verifier_id=execution_success}"
        ]
        == 0
    )
    assert (
        by_name[
            "failure.count{target=review1,impact=execution,kind=llm,"
            "phase=request,error_type=timeout}"
        ]
        == 1
    )
    assert Metric.from_dict(metrics[0].to_dict()) == metrics[0]
    assert TrajectoryAnalysisRun.from_dict(
        _run("roundtrip", 2, False).to_dict()
    ) == _run("roundtrip", 2, False)


def test_html_report_contains_catalog_failures_and_categorical_trends():
    runs = [_run("baseline", 1, True), _run("candidate", 3, False)]
    persisted_metrics = [metric for run in runs for metric in aggregate_metrics(run)]
    html = render_report_html(
        runs,
        title="Weekly trajectory quality",
        metrics=persisted_metrics,
    )

    assert "Weekly trajectory quality" in html
    assert "execution_success" in html
    assert "timeout" in html
    assert "trajectory-candidate" in html
    assert "APITimeoutError" in html
    assert '"type": "time"' in html
    assert "2026-08-01T00:00:00+00:00" in html
    assert "2026-08-03T00:00:00+00:00" in html


def test_html_counts_runs_and_exposes_target_dimension():
    html = render_report_html([_run("targeted", 2, True)])

    assert "Runs:</b> 1" in html
    assert "target=review1" in html
    assert '"type": "time"' not in html
