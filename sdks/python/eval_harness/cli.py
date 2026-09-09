"""CLI entry: ``python -m eval_harness.cli <experiment.yaml> [--mock] [--fresh]``.

Loads the experiment, resolves metrics, builds producers (mock echo, or the
live SUT adapter), then runs the reconcile + checkpoint + report
pipeline. ``--mock`` runs the whole thing with no live server (writes real
report files — useful to see the output shape).
"""

from __future__ import annotations

import argparse
import asyncio
from pathlib import Path

from eval_harness.config import load_experiment
from eval_harness.engine import run_experiment
from eval_harness.metric.registry import resolve


def _build_producers(exp, mock: bool):
    if mock:
        from eval_harness.produce.mock import EchoProvisioner, EchoSolver

        return EchoProvisioner(), EchoSolver()
    # eval_harness is the generic framework; the live Provisioner/Solver that
    # drives a live SUT is a consumer concern and lives in the consumer
    # project. For real runs, import eval_harness there, build your producers,
    # and call engine.run_experiment directly.
    raise SystemExit(
        "no built-in live producers: provide your own Provisioner/Solver "
        "(e.g. a live SUT adapter) and call eval_harness.engine.run_experiment, "
        "or pass --mock to run the echo producer here."
    )


async def _amain(args: argparse.Namespace) -> int:
    exp = load_experiment(args.experiment)
    metrics = resolve(exp.metrics)
    provisioner, solver = _build_producers(exp, args.mock)
    ws = await run_experiment(
        exp,
        provisioner,
        solver,
        metrics,
        runs_dir=args.runs_dir,
        fresh=args.fresh,
        checkpoint_interval=args.checkpoint_interval,
        config_path=args.experiment,  # snapshot the config into the run dir
    )
    run_dir = Path(args.runs_dir) / exp.name / ws.run_id
    if exp.description:
        print(f"[exp] {exp.name}: {exp.description}")
    print(f"[done] {exp.name} {ws.stats()} complete={ws.is_complete()}")
    print(f"[reports] {run_dir}  (results.csv, report*, experiment.yaml, verdict.json)")
    return 0


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    p.add_argument("experiment", help="path to experiments/<name>.yaml")
    p.add_argument("--mock", action="store_true", help="echo producer; no live server")
    p.add_argument("--fresh", action="store_true", help="ignore any existing checkpoint")
    p.add_argument("--runs-dir", default="runs", help="output root (default: ./runs)")
    p.add_argument("--checkpoint-interval", type=float, default=10.0)
    return asyncio.run(_amain(p.parse_args()))


if __name__ == "__main__":
    raise SystemExit(main())
