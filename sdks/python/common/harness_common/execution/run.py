"""The run-layout contract shared by the harness family.

One constraint, stated once here so it has an owner instead of living as
prose plus three hand-rolled paths:

- **one config = one experiment.** A single config/experiment yaml defines one experiment; a
  different yaml is a different experiment. ``scope`` is its name (the run dir's first
  segment) — harness-neutral: eval/perf use the experiment name, e2e a suite/service name.
- **one run = one run-id, unique within the scope.** Each execution of an experiment gets a
  run-id. How it is DERIVED is each harness's own call and deliberately NOT unified: perf/e2e
  and trajectory use a timestamp (every run a fresh history entry); eval uses
  ``experiment_hash`` (a
  same-config rerun lands in the SAME dir and resumes in place — a content-addressed reuse
  namespace, not a history snapshot). The shared contract is only that it is unique per scope.
- **Artifacts land under** ``runs/<scope>/<run-id>/``. The verdict.json + the rich artifacts
  (results.csv / run.json / report …) all sit there at one predictable depth, so a consumer
  globs ``runs/**/verdict.json`` regardless of harness. See ``spec/conventions.md`` 「Run
  产物与 verdict 出口」.

This is a *data/convention* seam, not a behavioural one. ``Experiment`` and
``ExperimentRun`` provide common identity, while engines remain domain-owned and
non-substitutable (eval's engine is a function, perf's a class, e2e is pytest-driven).
Run-id derivation also diverges on purpose, so each harness calls ``run_dir_for``.
"""

from __future__ import annotations

from pathlib import Path


def run_dir_for(runs_dir: str | Path, scope: str, run_id: str) -> Path:
    """``runs/<scope>/<run-id>/`` — the single source of truth for the run-dir layout. Every
    harness routes its run dir through here, so changing the layout means editing one place."""
    return Path(runs_dir) / scope / run_id
