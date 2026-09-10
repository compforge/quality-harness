"""Disk-backed batch results and exact grouped statistics without retaining trees."""

from __future__ import annotations

import json
import math
import sqlite3
import time
from collections.abc import AsyncIterator, Awaitable, Callable, Iterable, Iterator
from contextlib import AbstractAsyncContextManager
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from trace_harness.runtime import TraceSession

from dataclasses import asdict, dataclass, replace
from pathlib import Path
from uuid import uuid4

from harness_common.report_kit import Report, Section, Table, render_html

from trace_harness.analyze.context import AnalysisContext
from trace_harness.corpus.tables import _fact_rows, _trace_row
from trace_harness.dataset import Dataset
from trace_harness.model.node import Finding

BatchDetector = Callable[
    [Dataset, "BatchContext"], Iterable[Finding] | Awaitable[Iterable[Finding]]
]


def _numbers(value, prefix=""):
    if isinstance(value, dict):
        for key, item in value.items():
            yield from _numbers(item, f"{prefix}.{key}" if prefix else key)
    elif isinstance(value, float | int) and not isinstance(value, bool) and math.isfinite(value):
        yield prefix, value


@dataclass(frozen=True)
class BatchResult:
    path: Path

    def rows(self, name: str) -> Iterator[dict]:
        if name not in {"facts", "traces", "findings", "measurements", "failures"}:
            raise ValueError(f"unknown result table: {name}")
        with (self.path / f"{name}.jsonl").open() as stream:
            for line in stream:
                yield json.loads(line)

    @property
    def summary(self) -> dict:
        return json.loads((self.path / "summary.json").read_text())

    def compare(self, baseline: BatchResult) -> list[dict]:
        old = {(r["kind"], r["name"], r["metric"]): r for r in baseline.summary["metrics"]}
        return [
            {
                **row,
                "baseline_count": old[key]["count"],
                "baseline_p50": old[key]["p50"],
                "delta_p50": row["p50"] - old[key]["p50"],
                "baseline_p95": old[key]["p95"],
                "delta_p95": row["p95"] - old[key]["p95"],
            }
            for row in self.summary["metrics"]
            if (key := (row["kind"], row["name"], row["metric"])) in old
        ]


class BatchContext:
    """A detector may revisit trees or query the persisted measurements of this run."""

    def __init__(self, session: TraceSession, dataset: Dataset, result: BatchResult):
        self.session, self.dataset, self.result = session, dataset, result

    def trees(self, dataset: Dataset) -> AsyncIterator[AnalysisContext]:
        return self.session.trees(dataset)

    def tree(self, dataset: Dataset, trace_id: str) -> AbstractAsyncContextManager[AnalysisContext]:
        return self.session.tree(dataset, trace_id)

    def rows(self, name: str) -> Iterator[dict]:
        return self.result.rows(name)

    def emit(self, finding: Finding) -> None:
        with (self.result.path / "findings.jsonl").open("a") as stream:
            stream.write(json.dumps(asdict(finding), ensure_ascii=False) + "\n")


def _summary(db, coverage):
    metrics = []
    for kind, name, metric, count, average in db.execute(
        "SELECT kind,name,metric,count(*),avg(value) FROM values_ GROUP BY kind,name,metric"
    ):

        def quantile(q, count=count, kind=kind, name=name, metric=metric):
            position = (count - 1) * q
            lo = math.floor(position)
            values = [
                r[0]
                for r in db.execute(
                    "SELECT value FROM values_ WHERE kind=? AND name=? AND metric=? "
                    "ORDER BY value LIMIT 2 OFFSET ?",
                    (kind, name, metric, lo),
                )
            ]
            return values[0] + (values[-1] - values[0]) * (position - lo)

        metrics.append(
            dict(
                kind=kind,
                name=name,
                metric=metric,
                count=count,
                mean=average,
                p50=quantile(0.5),
                p95=quantile(0.95),
                p99=quantile(0.99),
            )
        )
    return {"coverage": coverage, "metrics": metrics}


def write_batch_report(result: BatchResult, *, comparison: list[dict] | None = None) -> None:
    summary = result.summary
    rows = [
        [r["kind"], r["name"], r["metric"], str(r["count"]), f"{r['p50']:.3f}", f"{r['p95']:.3f}"]
        for r in summary["metrics"]
    ]
    counts = {}
    for finding in result.rows("findings"):
        key = (finding["source"], finding["severity"])
        bucket = counts.setdefault(key, [0, set()])
        bucket[0] += 1
        if finding.get("trace_id"):
            bucket[1].add(finding["trace_id"])
    sections = [
        Section("指标分布", [Table(["kind", "name", "metric", "count", "p50", "p95"], rows)]),
        Section(
            "错误签名与规则命中",
            [
                Table(
                    ["source", "severity", "count", "traces"],
                    [[*key, str(value[0]), str(len(value[1]))] for key, value in counts.items()],
                )
            ],
        ),
    ]
    if comparison is not None:
        sections.append(
            Section(
                "Baseline / candidate",
                [
                    Table(
                        ["kind", "name", "metric", "Δp50", "Δp95"],
                        [
                            [
                                r["kind"],
                                r["name"],
                                r["metric"],
                                str(r["delta_p50"]),
                                str(r["delta_p95"]),
                            ]
                            for r in comparison
                        ],
                    )
                ],
            )
        )
    report = Report(
        title="trace corpus · batch",
        meta=[(k, str(v)) for k, v in summary["coverage"].items()],
        sections=sections,
    )
    (result.path / "report.html").write_text(render_html(report))


async def run_batch(
    session: TraceSession,
    dataset: Dataset,
    *,
    detectors: list[str] | None = None,
    metrics: list[str] | None = None,
    batch_detectors: list[str] | None = None,
) -> BatchResult:
    available = session.harness.contributions.batch_detectors
    selections = (
        ("detectors", detectors, session.harness.detectors.registered()),
        ("batch detectors", batch_detectors, available),
    )
    for label, selected, registered in selections:
        if selected is not None and (unknown := set(selected) - {d.__name__ for d in registered}):
            raise KeyError(f"unknown {label}: {sorted(unknown)}")
    if metrics is not None and (
        unknown := set(metrics) - {m.spec.id for m in session.harness.measurers}
    ):
        raise KeyError(f"unknown metrics: {sorted(unknown)}")
    path = session.work_dir / "runs" / uuid4().hex
    path.mkdir(parents=True)
    result = BatchResult(path)
    handles = {
        name: (path / f"{name}.jsonl").open("w")
        for name in ("facts", "traces", "findings", "measurements", "failures")
    }
    db = sqlite3.connect(path / "results.sqlite")
    db.execute("CREATE TABLE values_(kind TEXT,name TEXT,metric TEXT,value REAL)")
    db.execute("CREATE INDEX grouped_values ON values_(kind,name,metric,value)")
    coverage = {"selected": dataset.count, "succeeded": 0, "failed": 0}
    metric_statuses = {}
    started = time.monotonic()
    before_loading = dict(session._loader(dataset).stats)
    manifest = {
        "dataset": str(dataset.path),
        "dataset_id": dataset.id,
        "source": dataset.source,
        "detectors": detectors,
        "metrics": metrics,
        "batch_detectors": batch_detectors,
        "load": asdict(session.config),
        "status": "running",
    }
    (path / "manifest.json").write_text(json.dumps(manifest, indent=2))

    def write(name, row):
        handles[name].write(json.dumps(row, ensure_ascii=False) + "\n")

    try:
        for tid in dataset.members():
            try:
                async with session.tree(dataset, tid) as initial:
                    analysis = await session.analyze(
                        replace(initial, finding_limit=None), detectors=detectors, metrics=metrics
                    )
                    trace = analysis.trace
                    write("traces", _trace_row(trace, analysis.findings))
                    for row in _fact_rows(trace):
                        # Reports persist descriptive columns, not request bodies or prompts.
                        row = {
                            k: v
                            for k, v in row.items()
                            if v is None
                            or isinstance(v, int | float | bool)
                            or (isinstance(v, str) and len(v) <= 512)
                        }
                        write("facts", row)
                        for metric, value in _numbers(
                            {k: v for k, v in row.items() if k in trace.specs[row["kind"]].metrics}
                        ):
                            db.execute(
                                "INSERT INTO values_ VALUES(?,?,?,?)",
                                (row["kind"], row["name"], metric, value),
                            )
                    for node_id, measurements in analysis.measurements.results.items():
                        node = trace.view().by_id[node_id]
                        for measurement in measurements:
                            write("measurements", {"trace_id": tid, **asdict(measurement)})
                            statuses = metric_statuses.setdefault(measurement.spec_id, {})
                            statuses[measurement.status] = statuses.get(measurement.status, 0) + 1
                            if measurement.status == "measured":
                                for metric, value in _numbers(
                                    measurement.values, measurement.spec_id
                                ):
                                    db.execute(
                                        "INSERT INTO values_ VALUES(?,?,?,?)",
                                        (node.kind, node.name, metric, value),
                                    )
                    for findings in analysis.findings.values():
                        for finding in findings:
                            write(
                                "findings",
                                {"trace_id": tid, "node_id": finding.node_id, **asdict(finding)},
                            )
                    coverage["succeeded"] += 1
            except Exception as exc:
                coverage["failed"] += 1
                write("failures", {"trace_id": tid, "type": type(exc).__name__, "error": str(exc)})
            db.commit()
        for handle in handles.values():
            handle.flush()
        summary = _summary(db, coverage)
        summary["wall_clock_s"] = time.monotonic() - started
        summary["measurement_statuses"] = metric_statuses
        (path / "summary.json").write_text(json.dumps(summary, indent=2))
        context = BatchContext(session, dataset, result)
        for detector in available:
            if batch_detectors is not None and detector.__name__ not in batch_detectors:
                continue
            findings = detector(dataset, context)
            if isinstance(findings, Awaitable):
                findings = await findings
            for finding in findings or ():
                context.emit(finding)
        summary["wall_clock_s"] = time.monotonic() - started
        summary["loading"] = {
            key: value - before_loading[key]
            for key, value in session._loader(dataset).stats.items()
        }
        (path / "summary.json").write_text(json.dumps(summary, indent=2))
        (path / "meta.json").write_text(
            json.dumps(
                {
                    "format": "jsonl",
                    "metric_cols": sorted({r["metric"] for r in summary["metrics"]}),
                }
            )
        )
        manifest["status"] = "complete" if coverage["failed"] == 0 else "partial"
    except BaseException:
        manifest["status"] = "failed"
        raise
    finally:
        db.close()
        for handle in handles.values():
            handle.close()
        (path / "manifest.json").write_text(json.dumps(manifest, indent=2))
    write_batch_report(result)
    from trace_harness.analyze.verdict import write_verdict
    from trace_harness.corpus.tables import CorpusTables

    write_verdict(path, "batch", path.name, CorpusTables())
    return result
