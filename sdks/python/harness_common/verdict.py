"""Cross-harness ``verdict.json`` schema — the neutral wire shape (see
``spec/verdict-schema.yaml``), shared by e2e / eval / perf / trace / trajectory.

Harness-neutral by design: this holds the SERIALIZATION (the `Verdict` shape, omit-empty
`to_dict`, the rollup precedence) — never a harness's PROJECTION. Each SDK keeps its own thin
projector (its internal state → these `CaseVerdict`s) and calls `build_run_verdict` +
`write_verdict`. The run-dir layout it writes into lives in `common.run`. The SDKs depend
on `common` but not on each other.

Status vocabulary: pass / fail / error / skipped. `error` wins the rollup over `fail` — a
case that couldn't be judged (env/test broken) makes the tally incomplete, so the run is
"untrustworthy, fix first" rather than a partial pass. A harness that has no `fail` (eval —
quality isn't pass/fail) simply never emits it; the single precedence still orders its
{error, pass, skipped} correctly.

Two judged units, kept apart so a consumer never conflates them:
- `cases` (`CaseVerdict`) — per-case judgments, keyed by `case_id` (the cross-harness
  alignment key). e2e/eval fill these.
- `checks` (`CheckVerdict`) — run-level assertion gates on an aggregate/slice metric, a
  DIFFERENT kind of unit (a case is a stimulus; a check is an assertion on behavior).
  perf's SLO checks and trace's experiment gates fill these.
The wire serializes only these two source-of-truth lists (plus the run rollup) — never a
derived count. Status tallies are computed at read time (`RunVerdict.summary` / `summarize`),
so a count can't drift from the list it summarizes, and there is no single stored tally
spanning both unit kinds to ambiguously sum across runs.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal

from spec_case.model import (
    Face,
)  # the judgment-face enum, shared with the case model (input↔output)

# Trajectory evaluates recorded execution evidence rather than ``case.judge`` criteria,
# so it is a verdict-producing harness without becoming a canonical Case judgment face.
HarnessFace = Face | Literal["trajectory"]

SCHEMA_VERSION = 1

# The judgment-outcome vocabulary — one closed set used by every status field (run / case /
# check), so the rollup precedence below can order any of them. A typed alias rather than bare
# `str` so a stray status is a static error, not a silent value the rollup quietly drops.
Status = Literal["pass", "fail", "error", "skipped"]

# run-level rollup precedence (see module docstring on why error > fail)
PRECEDENCE: tuple[Status, ...] = ("error", "fail", "pass", "skipped")


def rollup_status(statuses: list[Status]) -> Status:
    """Combine many statuses into one, by ``PRECEDENCE``. Empty → skipped."""
    present = set(statuses)
    for s in PRECEDENCE:
        if s in present:
            return s
    return "skipped"


@dataclass
class CaseVerdict:
    case_id: str
    status: Status
    arm_id: str | None = None
    reason: str | None = None
    score: float | None = None
    facets: dict[str, str] = field(default_factory=dict)
    metrics: dict[str, dict] = field(default_factory=dict)

    def to_dict(self) -> dict:
        d: dict = {"case_id": self.case_id, "status": self.status}
        if self.arm_id:
            d["arm_id"] = self.arm_id
        if self.reason:
            d["reason"] = self.reason
        if self.score is not None:
            d["score"] = self.score
        if self.facets:
            d["facets"] = dict(self.facets)
        if self.metrics:
            d["metrics"] = dict(self.metrics)
        return d


@dataclass
class CheckVerdict:
    """One run-level assertion gate — a pass/fail check on an aggregate/slice metric, a
    DIFFERENT judged unit from a per-case verdict (a case is a stimulus; a check is an
    assertion on observed behavior). Two producers: perf's SLO checks (assertions on
    load-test aggregates) and trace's experiment gates (assertions on telemetry-derived
    tables — ``trace_harness/verdict.py``); a run-level eval threshold (overall score > x)
    would be a third. ``name`` is a stable label for the assertion; ``status`` uses the same
    four-state vocab as a case (producers emit only pass/fail/skipped). ``metric`` /
    ``observed`` carry what was asserted vs seen so a consumer can render the gap.

    Placement: with two producers the repo's "two instances before you name a pattern" bar
    (see ``common.overlay``) is met — the dataclass stays here as part of the shared wire
    schema; the earlier "move back to perf if no second producer appears" backlog is closed.
    """

    name: str
    status: Status
    metric: str | None = None
    observed: float | None = None
    reason: str | None = None
    arm_id: str | None = None
    window_id: str | None = None

    def to_dict(self) -> dict:
        d: dict = {"name": self.name, "status": self.status}
        if self.arm_id:
            d["arm_id"] = self.arm_id
        if self.window_id:
            d["window_id"] = self.window_id
        if self.metric:
            d["metric"] = self.metric
        if self.observed is not None:
            d["observed"] = self.observed
        if self.reason:
            d["reason"] = self.reason
        return d


@dataclass
class RunVerdict:
    harness: HarnessFace
    scope: str
    run_id: str
    status: Status
    cases: list[CaseVerdict] = field(default_factory=list)
    checks: list[CheckVerdict] = field(default_factory=list)
    reason: str | None = None
    score: float | None = None
    metrics: dict[str, dict] = field(default_factory=dict)
    artifact_paths: dict[str, str] = field(default_factory=dict)
    created_at: str | None = None

    @property
    def summary(self) -> dict[str, int]:
        """Status counts over ``cases`` — DERIVED, never stored or serialized. The wire file
        carries only the source-of-truth lists (``cases`` / ``checks``); this is just the
        in-process convenience (same numbers ``summarize`` returns). It counts cases only —
        run-level ``checks`` are tallied off ``checks`` directly."""
        return summarize(self.cases)

    def to_dict(self) -> dict:
        # The wire carries only source-of-truth: the judged-unit lists + the run rollup.
        # Derived tallies (`summary`) are deliberately NOT serialized — a stored count can
        # drift from the list it summarizes, and one tally over two unit kinds (cases vs
        # checks) is exactly the cross-harness ambiguity we refuse to bake into the file.
        # A consumer that wants counts folds `cases` / `checks` itself.
        d: dict = {
            "schema_version": SCHEMA_VERSION,
            "harness": self.harness,
            "scope": self.scope,
            "run_id": self.run_id,
            "status": self.status,
        }
        if self.score is not None:
            d["score"] = self.score
        if self.reason:
            d["reason"] = self.reason
        if self.metrics:
            d["metrics"] = dict(self.metrics)
        if self.cases:
            d["cases"] = [c.to_dict() for c in self.cases]
        if self.checks:
            d["checks"] = [c.to_dict() for c in self.checks]
        if self.artifact_paths:
            d["artifact_paths"] = dict(self.artifact_paths)
        if self.created_at:
            d["created_at"] = self.created_at
        return d


def summarize(cases: list[CaseVerdict]) -> dict[str, int]:
    """Count cases by status → {total, pass, fail, error, skipped}."""
    summary = {"total": len(cases), "pass": 0, "fail": 0, "error": 0, "skipped": 0}
    for c in cases:
        if c.status in summary:
            summary[c.status] += 1
    return summary


def rollup_reason(cases: list[CaseVerdict], run_status: Status) -> str | None:
    """One-line why for a non-pass run: headline counts + first offending case."""
    if run_status == "pass":
        return None
    counts = summarize(cases)
    parts = [f"{counts[k]} {k}" for k in ("fail", "error", "skipped") if counts[k]]
    headline = ", ".join(parts) or run_status
    first = next((c for c in cases if c.status == run_status), None)
    if first is not None:
        detail = f"{first.case_id}: {first.reason}" if first.reason else first.case_id
        return f"{headline}; first {run_status} — {detail}"
    return headline


def build_run_verdict(
    harness: HarnessFace,
    scope: str,
    run_id: str,
    cases: list[CaseVerdict],
    *,
    checks: list[CheckVerdict] | None = None,
    status: Status | None = None,
    reason: str | None = None,
    score: float | None = None,
    metrics: dict[str, dict] | None = None,
    artifact_paths: dict[str, str] | None = None,
    created_at: str | None = None,
) -> RunVerdict:
    """Assemble a run verdict. Defaults derive from the judged units (status = rollup over
    ``cases`` + ``checks``, reason = rollup_reason on a non-pass run); a harness whose
    run-level judgement has its own skip semantics (perf's SLO gate) passes ``status`` /
    ``reason`` explicitly. Status counts are not an input — they're derived from ``cases`` at
    read time (``RunVerdict.summary`` / ``summarize``), never stored on the wire."""
    checks = checks or []
    status = status or rollup_status(
        [c.status for c in cases] + [k.status for k in checks]
    )
    # auto-reason only when there ARE cases to point at; a run-level judgement (perf's SLO
    # gate, cases=[]) passes its own reason or leaves it empty.
    if reason is None and status != "pass" and cases:
        reason = rollup_reason(cases, status)
    return RunVerdict(
        harness=harness,
        scope=scope,
        run_id=run_id,
        status=status,
        cases=cases,
        checks=checks,
        reason=reason,
        score=score,
        metrics=metrics or {},
        artifact_paths=artifact_paths or {},
        created_at=created_at,
    )


def write_verdict(run_dir: str | Path, verdict: RunVerdict) -> Path:
    """Write ``verdict.json`` into ``run_dir`` (created if needed). Returns its path."""
    out = Path(run_dir)
    out.mkdir(parents=True, exist_ok=True)
    path = out / "verdict.json"
    path.write_text(
        json.dumps(verdict.to_dict(), ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    return path
