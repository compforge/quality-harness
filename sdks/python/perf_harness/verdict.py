"""Project a perf Run into the cross-harness ``verdict.json`` (spec/verdict-schema.yaml).

The schema/serialization lives in `common.verdict`; this keeps only perf's projection.
perf judges at the *run* level via the SLO gate, and its judged unit is the **SLO check**
(an assertion on an aggregate / facet slice), NOT a case — so the verdict carries no
``cases`` rows; each SLO check becomes a `common.verdict.CheckVerdict` in ``checks``.
``summary`` (which always counts cases) is therefore omitted; perf's scale is ``len(checks)``.
"""

from __future__ import annotations

from pathlib import Path

from harness_common import verdict as _v

from perf_harness.model import ArmRun, Run, SloCheck


def _check_verdict(c: SloCheck, arm_id: str) -> _v.CheckVerdict:
    """One SLO check → a CheckVerdict. ``skipped`` iff the metric's slice had no value
    (``observed is None``) — a skip is not a pass; otherwise the check's own state."""
    a = c.assertion
    name = f"{a.metric} {a.op} {a.threshold}"
    name += f" [window={c.window_id or a.window.kind}]"
    status = "skipped" if c.observed is None else c.state
    return _v.CheckVerdict(
        name=name,
        status=status,
        metric=a.metric,
        observed=c.observed,
        arm_id=arm_id,
        window_id=c.window_id,
    )


def _build(run: Run) -> _v.RunVerdict:
    """Run → RunVerdict. Early-stopped arm_runs fail before SLO rollup because their
    partial windows cannot verify a load level. Otherwise status follows the recorded
    SLO checks (fail > pass > skipped), deliberately not ``run.passed`` because the
    engine's default skip policy is lenient while persisted evidence must not read
    green when no assertion was verified."""
    raw: list[SloCheck] = [c for t in run.arm_runs for c in t.slo]
    error_arm_runs = [t for t in run.arm_runs if t.phase_errors]
    early = [t for t in run.arm_runs if t.stop.early and not t.phase_errors]
    n_pass = sum(1 for c in raw if c.state == "pass")
    n_fail = sum(1 for c in raw if c.state == "fail" and c.observed is not None)
    cooldown_skips = [
        c for c in raw if c.state == "skipped" and c.assertion.window.kind == "cooldown"
    ]

    if error_arm_runs:
        status = "error"
    elif not run.arm_runs:
        status = "skipped"
    elif early or any(t.stop.interrupted for t in run.arm_runs) or n_fail or cooldown_skips:
        status = "fail"
    elif n_pass:
        status = "pass"
    else:
        status = "skipped"  # SLO declared but every check skipped, or no SLO at all
    if error_arm_runs:
        reason = _phase_error_reason(error_arm_runs)
    elif early:
        reason = _early_stop_reason(early)
    elif any(t.stop.interrupted for t in run.arm_runs):
        reason = "requests interrupted before completion"
    elif n_fail:
        reason = _fail_reason([c for c in raw if c.state == "fail" and c.observed is not None])
    elif cooldown_skips:
        reason = f"{len(cooldown_skips)} cooldown SLO skipped; recovery was not observed"
    else:
        reason = None

    return _v.build_run_verdict(
        "perf",
        run.experiment,
        run.run_id,
        [],
        checks=[_check_verdict(c, arm_run.arm.id) for arm_run in run.arm_runs for c in arm_run.slo],
        status=status,
        reason=reason,
        artifact_paths={"run": "run.json", "report": "report.md"},
        created_at=run.created_at,
    )


def _fail_reason(failed: list[SloCheck]) -> str:
    first = failed[0]
    a = first.assertion
    window = f" [{first.window_id or a.window.kind}]"
    detail = f"{a.metric}{window} {a.op} {a.threshold} (observed {first.observed})"
    return f"{len(failed)} SLO failed; first — {detail}"


def _early_stop_reason(arm_runs: list[ArmRun]) -> str:
    stop = arm_runs[0].stop
    detail = stop.reason
    if stop.snapshot is not None:
        snap = stop.snapshot
        detail += f" at {snap.at_s:.1f}s ({snap.errors}/{snap.completed} errors)"
    return f"{len(arm_runs)} arm_run(s) stopped early; first — {detail}"


def _phase_error_reason(arm_runs: list[ArmRun]) -> str:
    first = arm_runs[0].phase_errors[0]
    detail = f"{first.phase}: {first.error_type}"
    if first.message:
        detail += f": {first.message}"
    count = sum(len(arm_run.phase_errors) for arm_run in arm_runs)
    return f"{count} arm_run phase error(s); first — {detail}"


def build_verdict_doc(run: Run) -> dict:
    return _build(run).to_dict()


def write_verdict(run_dir: str | Path, run: Run) -> Path:
    """Write ``verdict.json`` into ``run_dir``. Returns its path."""
    return _v.write_verdict(run_dir, _build(run))
