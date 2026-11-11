"""Execution diagnostics projected into the common artifact contract; no acceptance gate."""

from pathlib import Path

from harness_common.execution import verdict as _v

from perf_harness.model import Run


def _build(run: Run) -> _v.RunVerdict:
    # A healthy experiment has no business verdict. Its lifecycle status is in run.json.
    errors = [e for arm in run.arm_runs for e in arm.phase_errors]
    reason = (
        f"{errors[0].phase}: {errors[0].error_type}: {errors[0].message}"
        if errors
        else f"execution {run.status}; no acceptance policy"
    )
    return _v.build_run_verdict(
        "perf",
        run.experiment,
        run.run_id,
        [],
        status="error" if errors else "skipped",
        reason=reason,
        artifact_paths={"run": "run.json", "report": "report.md"},
        created_at=run.created_at,
    )


def build_verdict_doc(run: Run) -> dict:
    return _build(run).to_dict()


def write_verdict(run_dir: str | Path, run: Run) -> Path:
    return _v.write_verdict(run_dir, _build(run))
