"""Orchestrator: build-or-resume the Worksheet, reconcile under a checkpointer,
write reports. The one place the layers are wired together.

``run_experiment`` is producer-injected (provisioner / solver / metrics), so it
runs end-to-end with mocks (no live services) and with a real SUT adapter
adapter unchanged. Resume is automatic: an existing checkpoint is loaded (guarded
by ``experiment_hash``) and only its gaps are filled.
"""

from __future__ import annotations

import shutil
from datetime import datetime
from pathlib import Path

from harness_common.overlay import Overlay
from harness_common.run import run_dir_for

from eval_harness.metric.base import BaseMetric
from eval_harness.model.experiment import Experiment
from eval_harness.report.html import compare_report_html, single_report_html
from eval_harness.report.render import (
    compare_report_md,
    digest_csv,
    failures_csv,
    results_csv,
    single_report_md,
)
from eval_harness.schedule.ratelimit import GateRegistry
from eval_harness.schedule.reconcile import Provisioner, ReconcileEngine, Solver
from eval_harness.verdict import write_verdict
from eval_harness.worksheet.checkpoint import Checkpointer, from_jsonl, to_jsonl
from eval_harness.worksheet.worksheet import CellState, Worksheet


async def teardown_provisions(ws: Worksheet, provisioner: Provisioner) -> list[str]:
    """Best-effort ``clean`` of every provisioned resource (the teardown phase).

    Opt-in — NOT run automatically, because the heavy layer is built to be reused across
    runs/resumes (cleaning it would force re-provisioning next time). Call this, or pass
    ``teardown=True`` to ``run_experiment``, only when you're done with the resources.
    Returns the keys whose clean failed (surface them as possible leaks); never raises.
    """
    failed: list[str] = []
    for p in list(ws.provisions.values()):
        if p.state is CellState.OK and p.subject_id:
            try:
                await provisioner.clean(p.key, p.subject_id)
            except Exception:  # noqa: BLE001 — best-effort; one failure must not block the rest
                failed.append(p.key)
    return failed


def resolve_weights(exp: Experiment, metrics: list[BaseMetric]) -> dict[str, float]:
    """Per-quality-metric weight = experiment override else the metric's default.

    The weights are an experiment ``Overlay`` over the scored-metric catalog: ``measure``
    metrics carry no weight, so they're filtered out first and a weight naming one (or a
    typo'd metric) is rejected, not silently dropped. A ``DIAGNOSTIC`` metric defaults to 0
    (reported, out of the composite) unless explicitly weighted — so adding one never shifts
    the headline score.
    """
    scored = {m.NAME: m for m in metrics if m.KIND != "measure"}
    return Overlay(exp.weights).resolve(
        scored, default_of=lambda name: 0.0 if scored[name].DIAGNOSTIC else scored[name].WEIGHT
    )


def write_reports(
    ws: Worksheet,
    exp: Experiment,
    metrics: list[BaseMetric],
    exp_dir: Path,
    config_path: str | Path | None = None,
) -> Path:
    weights = resolve_weights(exp, metrics)
    schema = exp.facet_schema()
    desc = exp.description
    # metric name → its Chinese DESCRIPTION, for the HTML report's column-header tooltips.
    mdesc = {m.NAME: m.DESCRIPTION for m in metrics if m.DESCRIPTION}
    # Report generation time, in the local OS timezone (human-facing — readers expect their
    # own wall clock, not UTC). Stamped here, not inside the pure render fns, so those stay
    # deterministic for snapshot tests.
    generated_at = datetime.now().astimezone().strftime("%Y-%m-%d %H:%M:%S %Z")
    exp_dir.mkdir(parents=True, exist_ok=True)
    # Snapshot the source config into the run dir so a run is self-describing ("what ran").
    # Copy the RAW file (``${ENV}`` placeholders intact) — never re-serialize the loaded
    # Experiment, whose llm.api_key is already interpolated (would leak the secret).
    if config_path:
        shutil.copyfile(config_path, exp_dir / "experiment.yaml")
    (exp_dir / "results.csv").write_text(results_csv(ws, weights), encoding="utf-8")
    # slim, eyeball-friendly companion: query + answer + metric scores only
    (exp_dir / "results_digest.csv").write_text(digest_csv(ws, weights), encoding="utf-8")
    # triage companion: only the no-answer / any-0-score rows, with judgements (the why)
    (exp_dir / "results_failures.csv").write_text(failures_csv(ws, weights), encoding="utf-8")
    arms = sorted({r.arm_id for r in ws.rows.values()})
    # Single arm_id → a flat report.md. Multiple arms → a report/ folder (the folder earns
    # its keep only when there's a comparison + one file per arm_id).
    if len(arms) <= 1:
        arm_id = arms[0] if arms else "default"
        out = exp_dir / "report.md"
        out.write_text(
            single_report_md(ws, arm_id, weights, schema, generated_at, desc), encoding="utf-8"
        )
        # HTML twin (sortable tables + score heat + metric-header tooltips); fully offline.
        (exp_dir / "report.html").write_text(
            single_report_html(ws, arm_id, weights, schema, generated_at, desc, mdesc),
            encoding="utf-8",
        )
        return out
    report_dir = exp_dir / "report"
    report_dir.mkdir(parents=True, exist_ok=True)
    (report_dir / "comparison.md").write_text(
        compare_report_md(ws, weights, schema, generated_at, desc), encoding="utf-8"
    )
    (report_dir / "comparison.html").write_text(
        compare_report_html(ws, weights, schema, generated_at, desc, mdesc), encoding="utf-8"
    )
    for arm_id in arms:
        (report_dir / f"{arm_id}.md").write_text(
            single_report_md(ws, arm_id, weights, schema, generated_at, desc), encoding="utf-8"
        )
        (report_dir / f"{arm_id}.html").write_text(
            single_report_html(ws, arm_id, weights, schema, generated_at, desc, mdesc),
            encoding="utf-8",
        )
    return report_dir


async def run_experiment(
    exp: Experiment,
    provisioner: Provisioner,
    solver: Solver,
    metrics: list[BaseMetric],
    *,
    runs_dir: str | Path,
    gates: GateRegistry | None = None,
    fresh: bool = False,
    checkpoint_interval: float = 10.0,
    config_path: str | Path | None = None,
    run_id: str | None = None,
    teardown: bool = False,
) -> Worksheet:
    # run dir = runs/<experiment>/<run-id>/. run-id defaults to experiment_hash, so a
    # same-config rerun lands in the SAME dir and resumes in place (reuse namespace
    # unchanged); an explicit run_id forces a fresh namespace. Aligns eval with
    # perf/e2e's runs/<scope>/<run-id>/ so the cross-harness verdict.json sits at one
    # predictable depth.
    rid = run_id or exp.experiment_hash()
    run_dir = run_dir_for(runs_dir, exp.name, rid)
    run_dir.mkdir(parents=True, exist_ok=True)
    ckpt = run_dir / "worksheet.jsonl"

    if not fresh and ckpt.exists():
        ws = from_jsonl(ckpt)
        if ws.experiment_hash != exp.experiment_hash():
            raise RuntimeError(
                f"checkpoint at {ckpt} has a different experiment_hash "
                f"(experiment yaml changed?); rerun with fresh=True to discard it"
            )
    else:
        ws = Worksheet.build(exp, run_id=rid)
    ws.add_artifact("worksheet", "worksheet.jsonl")

    engine = ReconcileEngine(ws, exp, provisioner, solver, metrics, gates=gates)
    async with Checkpointer(ws, ckpt, interval_s=checkpoint_interval):
        await engine.run()
    write_reports(ws, exp, metrics, run_dir, config_path=config_path)
    ws.add_artifact("results", "results.csv")
    write_verdict(run_dir, ws, resolve_weights(exp, metrics))  # cross-harness verdict.json
    to_jsonl(ws, ckpt)
    if teardown:
        await teardown_provisions(ws, provisioner)  # opt-in: clean provisioned resources
    return ws
