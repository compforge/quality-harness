"""CLI — ``python -m perf_harness.cli run <config.yaml>`` / ``analyze`` / ``report``.

``run``: each run lands under ``<runs_dir>/<run_id>/`` (report + csvs + the model
layer run.json/outcomes.jsonl); repeated runs accumulate instead of clobbering.
``--mock`` swaps in MockWorkload for an offline smoke; ``--run-id`` names the run.

``analyze``: deterministic observations over a finished run dir (reads the model
layer, never the HTML) — the mechanical part of a perf analysis, pre-chewed.

``report``: RE-RENDER the report artifacts from a run dir's model layer — the
rendering is a pure downstream of run.json/timeseries.csv, so a report-layout
upgrade applies to old runs without re-pressing anything.
"""

from __future__ import annotations

import argparse
import asyncio

from perf_harness.config import load_experiment
from perf_harness.engine import Engine
from perf_harness.report import write_run


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="perf_harness")
    sub = ap.add_subparsers(dest="cmd", required=True)
    run = sub.add_parser("run", help="run a perf experiment from a YAML config")
    run.add_argument("config")
    run.add_argument("--out", default=None, help="override runs_dir")
    run.add_argument("--run-id", default=None, help="name this run (default: timestamp)")
    run.add_argument("--mock", action="store_true", help="use MockWorkload (offline smoke)")
    an = sub.add_parser("analyze", help="deterministic observations over a run dir")
    an.add_argument("run_dir", help="runs/<experiment>/<run_id>/ (needs run.json)")
    rep = sub.add_parser("report", help="re-render report artifacts from a run dir")
    rep.add_argument("run_dir", help="runs/<experiment>/<run_id>/ (needs run.json)")
    args = ap.parse_args(argv)

    if args.cmd == "analyze":
        from perf_harness.analysis import analyze_run, render_text

        print(render_text(analyze_run(args.run_dir)), end="")
        return 0

    if args.cmd == "report":
        from pathlib import Path

        import yaml

        from perf_harness.config import _load_caseset_ref, _parse_facet_order
        from perf_harness.report import write_report
        from perf_harness.runio import load_run

        run_dir = Path(args.run_dir)
        facet_order: dict[str, list[str]] = {}
        cfg = run_dir / "config.yaml"  # the run's config snapshot carries the facet order
        if cfg.exists():
            raw = yaml.safe_load(cfg.read_text()) or {}
            caseset = _load_caseset_ref(raw.get("caseset"), cfg.parent)
            facet_order = (
                caseset.facet_schema.ordered_value_lists()
                if caseset
                else _parse_facet_order(raw.get("facets"))
            )
        loaded = load_run(run_dir)
        paths = write_report(loaded.trials, str(run_dir), facet_order=facet_order)
        print(f"re-rendered from model layer: {paths['report']}")
        print(f"  html: {paths['report_html']}")
        return 0

    experiment, runs_dir = load_experiment(args.config, mock=args.mock)
    runs_dir = args.out or runs_dir

    engine = Engine(experiment, run_id=args.run_id)
    run_result = asyncio.run(engine.run())
    paths = write_run(
        run_result,
        runs_dir,
        facet_order=experiment.facet_order,
        config_path=args.config,
    )

    print(
        f"experiment {run_result.experiment} · run {run_result.run_id} "
        f"— {len(run_result.trials)} trial(s)"
    )
    print(f"  → {paths['run_dir']}")
    for r in run_result.trials:
        if r.phase_errors:
            error = r.phase_errors[0]
            print(
                f"  {r.arm.resources.label():<14} {r.arm.load.label():<8} "
                f"ERROR {error.phase}: {error.error_type}: {error.message}"
            )
            continue
        o = r.measurement.request
        print(
            f"  {r.arm.resources.label():<14} {r.arm.load.label():<8} "
            f"rps={o.throughput_rps:6.1f} err={o.error_rate * 100:5.1f}% "
            f"p99={o.p99_ms:6.0f}ms"
        )
    print(f"report: {paths['report']}")
    if paths.get("report_html"):
        print(f"  html: {paths['report_html']}")
    errors = [r for r in run_result.trials if r.phase_errors]
    early = [r for r in run_result.trials if r.stop.early and not r.phase_errors]
    if errors:
        print(f"run: ERROR ({len(errors)} trial(s) had phase errors)")
    elif early:
        reasons = ", ".join(sorted({r.stop.reason for r in early}))
        print(f"run: FAIL ({len(early)} trial(s) stopped early: {reasons})")
    elif run_result.trials and any(r.slo for r in run_result.trials):
        skipped = sum(1 for r in run_result.trials for c in r.slo if c.observed is None)
        note = f" ({skipped} skipped — metric/slice absent; check label typos)" if skipped else ""
        print(f"SLO: {'PASS' if run_result.passed else 'FAIL'}{note}")
    # non-zero exit on an incomplete trial or SLO failure → CI gate
    return 0 if run_result.passed else 1


if __name__ == "__main__":
    raise SystemExit(main())
