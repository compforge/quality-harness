"""CLI — run, analyze, report and collect-metrics for performance experiments.

``run``: each run lands under ``<runs_dir>/<run_id>/`` (report + csvs + the model
layer run.json/requests.jsonl); repeated runs accumulate instead of clobbering.
``--mock`` swaps in MockRunner for an offline smoke; ``--run-id`` names the run.

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
    run.add_argument("--mock", action="store_true", help="use MockRunner (offline smoke)")
    an = sub.add_parser("analyze", help="deterministic observations over a run dir")
    an.add_argument("run_dir", help="runs/<experiment>/<run_id>/ (needs run.json)")
    rep = sub.add_parser("report", help="re-render report artifacts from a run dir")
    rep.add_argument("run_dir", help="runs/<experiment>/<run_id>/ (needs run.json)")
    collect = sub.add_parser(
        "collect-metrics", help="re-query saved Prometheus windows without sending load"
    )
    collect.add_argument("run_dir")
    collect.add_argument(
        "--config", help="YAML containing prometheus access; defaults to the run config snapshot"
    )
    args = ap.parse_args(argv)

    if args.cmd == "collect-metrics":
        from perf_harness.observe.history import recollect_metrics
        from perf_harness.report import write_report

        result = asyncio.run(recollect_metrics(args.run_dir, args.config))
        paths = write_report(
            result.arm_runs, args.run_dir, columns=result.report_columns, run=result
        )
        errors = sum(
            q.error is not None for arm in result.arm_runs for q in arm.prometheus_observations
        )
        print(f"metric collection: {errors} failed queries; report: {paths['report']}")
        return 1 if errors else 0

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
        paths = write_report(
            loaded.arm_runs,
            str(run_dir),
            facet_order=facet_order,
            columns=loaded.report_columns,
            run=loaded,
        )
        print(f"re-rendered from model layer: {paths['report']}")
        print(f"  html: {paths['report_html']}")
        return 0

    experiment, runs_dir = load_experiment(args.config, mock=args.mock)
    runs_dir = args.out or runs_dir

    engine = Engine(experiment, run_id=args.run_id)
    run_result = asyncio.run(engine.run(collect=False))
    paths = write_run(
        run_result,
        runs_dir,
        facet_order=experiment.facet_order,
        config_path=args.config,
    )

    if any(arm.prometheus_observations for arm in run_result.arm_runs):
        from pathlib import Path

        from perf_harness.observe.history import archive_collection
        from perf_harness.report import write_report
        from perf_harness.runio import write_metric_data

        # Query plans and request evidence already exist if collection is interrupted.
        asyncio.run(engine.collect_metrics(run_result))
        archive_collection(run_result, Path(paths["run_dir"]))
        write_metric_data(run_result, paths["run_dir"])
        paths.update(
            write_report(
                run_result.arm_runs,
                paths["run_dir"],
                facet_order=experiment.facet_order,
                columns=run_result.report_columns,
                run=run_result,
            )
        )

    print(
        f"experiment {run_result.experiment} · run {run_result.run_id} "
        f"— {len(run_result.arm_runs)} arm_run(s)"
    )
    print(f"  → {paths['run_dir']}")
    for r in run_result.arm_runs:
        if r.phase_errors:
            error = r.phase_errors[0]
            print(
                f"  {r.arm.resources.label():<14} {r.arm.load.label():<8} "
                f"ERROR {error.phase}: {error.error_type}: {error.message}"
            )
            continue
        o = r.full.request
        print(
            f"  {r.arm.resources.label():<14} {r.arm.load.label():<8} "
            f"rps={o.throughput_rps:6.1f} err={o.error_rate * 100:5.1f}% "
            f"p99={o.p99_ms:6.0f}ms"
        )
    print(f"report: {paths['report']}")
    if paths.get("report_html"):
        print(f"  html: {paths['report_html']}")
    print(f"execution: {run_result.status}")
    # Exit status describes execution, never business acceptance.
    return {"completed": 0, "stopped": 2, "error": 1}[run_result.status]


if __name__ == "__main__":
    raise SystemExit(main())
