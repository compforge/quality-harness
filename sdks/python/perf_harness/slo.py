"""SLO evaluation — the per-run gate (aggregate verdict), distinct from the
per-request ``Judge``.

Pure functions over already-aggregated ``ArmRun``s: read each metric through
the ``MetricStore`` (the one addressing face) and compare to a threshold, producing
THREE-state ``SloCheck``s. Purity means a run's pass/fail can be recomputed offline.

A metric ref is ``<name>{labels}.<stat>`` (or an undotted builtin alias like
``p99_ms``). Service/facet labels select entities; ``WindowSelector`` selects time. A
``Missing`` read (the slice/metric was absent on this arm_run) becomes a *skipped*
check: NOT a pass. It never lets a arm_run count as confirmed capacity, and under
``strict_slo`` it fails the run (a Prometheus alert may no-fire on empty; a CI gate
must not silently pass). The builtin descriptors + the request-slice projection
live in ``store.py`` (the metric layer); import them from there.
"""

from __future__ import annotations

from perf_harness.metric import Missing
from perf_harness.metric.store import MetricStore
from perf_harness.model import ArmRun, SloAssertion, SloCheck


def _compare(observed: float, op: str, threshold: float | tuple[float, float]) -> bool:
    if op == "between":
        lo, hi = threshold  # type: ignore[misc]
        return lo <= observed <= hi
    return {
        "lt": observed < threshold,
        "lte": observed <= threshold,
        "gt": observed > threshold,
        "gte": observed >= threshold,
    }[op]


def evaluate_slo(arm_run: ArmRun, assertions: list[SloAssertion]) -> list[SloCheck]:
    """Evaluate each assertion once per matching Window."""
    store = MetricStore([arm_run])
    checks: list[SloCheck] = []
    for a in assertions:
        windows = [window for window in arm_run.windows if a.window.matches(window)]
        if not windows:
            checks.append(SloCheck(a, observed=None, state="skipped", window_id=None))
        for window in windows:
            read = store.query(arm_run, a.metric, window)
            if isinstance(read, Missing):
                checks.append(SloCheck(a, observed=None, state="skipped", window_id=window.id))
                continue
            passed = _compare(read, a.op, a.threshold)
            checks.append(
                SloCheck(
                    a,
                    observed=read,
                    state="pass" if passed else "fail",
                    window_id=window.id,
                )
            )
    return checks


def slo_aware_capacity(arm_runs: list[ArmRun]) -> dict[str, float | None]:
    """Highest complete hold Window whose own checks all passed, per resources."""
    best: dict[str, float | None] = {}
    for arm_run in arm_runs:
        c = f"{arm_run.arm.resources.label()}|{arm_run.arm.load.mode}"
        best.setdefault(c, None)
        checks_by_window: dict[str, list[SloCheck]] = {}
        for check in arm_run.slo:
            if check.window_id is not None:
                checks_by_window.setdefault(check.window_id, []).append(check)
        for window in arm_run.windows:
            checks = checks_by_window.get(window.id, [])
            if (
                window.kind != "hold"
                or not window.complete
                or window.target_level is None
                or window.request is None
                or window.request.n == 0
                or window.request.n_dropped > 0
                or window.request.n_interrupted > 0
                or "incomplete" in window.request.caveats
                or not checks
                or not all(check.passed for check in checks)
            ):
                continue
            if best[c] is None or window.target_level > best[c]:
                best[c] = window.target_level
    return best
