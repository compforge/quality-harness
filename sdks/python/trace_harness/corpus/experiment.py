"""Run a file-backed Dataset from YAML and persist measurements, findings and a report.

Optional baseline comparison uses grouped statistics; legacy table gates materialize
only when requested. Online source construction belongs to the consuming application.
"""

from __future__ import annotations

from pathlib import Path

import yaml

from trace_harness.analyze.verdict import write_verdict
from trace_harness.corpus.operators import diff_runs
from trace_harness.corpus.store import read_tables


def _resolve_files(source: dict, base: Path) -> list[Path]:
    def _abs(p: str) -> Path:
        q = Path(p)
        return q if q.is_absolute() else base / q

    if "jaeger_dir" in source:
        return sorted(_abs(source["jaeger_dir"]).glob("*.jsonl"))
    if "jaeger_files" in source:
        return [_abs(f) for f in source["jaeger_files"]]
    raise ValueError("source 需要 jaeger_dir 或 jaeger_files")


async def run_experiment(exp_path: str | Path, runs_dir: str | Path = "runs") -> Path:
    from trace_harness import TraceContributions, TraceHarness
    from trace_harness.batch import BatchResult, write_batch_report
    from trace_harness.ingest.sources.base import SpanQuery
    from trace_harness.ingest.sources.jaeger_file import JaegerFileSource
    from trace_harness.kinds import genai
    from trace_harness.loading.model import LoadConfig

    exp_path = Path(exp_path)
    cfg = yaml.safe_load(exp_path.read_text()) or {}
    source_config = cfg.get("source") or {}
    files = _resolve_files(source_config, exp_path.parent)
    source = JaegerFileSource(files, index_dir=Path(runs_dir) / "source-index")
    harness = TraceHarness(TraceContributions(specs=tuple(genai.specs())))
    async with harness.open(
        source, work_dir=runs_dir, config=LoadConfig(**(cfg.get("load") or {}))
    ) as session:
        dataset = await session.select(SpanQuery(**(cfg.get("select") or {})))
        result = await session.detect(
            dataset,
            detectors=cfg.get("detectors"),
            metrics=cfg.get("metrics"),
            batch_detectors=cfg.get("batch_detectors"),
        )
        comparison = (
            result.compare(BatchResult(_resolve_baseline(cfg["diff"], exp_path.parent)))
            if cfg.get("diff")
            else None
        )
        if comparison is not None:
            write_batch_report(result, comparison=comparison)
        if cfg.get("gates"):
            # Existing table-based gates remain an explicit, opt-in materialization.
            tables = read_tables(result.path)
            diff_result = (
                diff_runs(read_tables(_resolve_baseline(cfg["diff"], exp_path.parent)), tables)
                if cfg.get("diff")
                else None
            )
            write_verdict(
                result.path,
                cfg.get("name", exp_path.stem),
                result.path.name,
                tables,
                gates=cfg["gates"],
                diff_result=diff_result,
            )
        return result.path


def _resolve_baseline(p: str, base: Path) -> Path:
    q = Path(p)
    return q if q.is_absolute() else base / q
