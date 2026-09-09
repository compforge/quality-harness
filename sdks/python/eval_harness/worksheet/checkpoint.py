"""Worksheet persistence: snapshot ⇄ jsonl, plus an async periodic checkpointer.

The Worksheet is the resumable state, so it is checkpointed to disk: a crash is
recovered by ``load`` + continuing the reconcile. Format is jsonl — first line a
``meta`` record (experiment / hash / metric names / provisions), then one
``row`` record per (arm_id × case). Writes are atomic (temp file + replace) so a
crash mid-flush never corrupts the checkpoint.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import os
from pathlib import Path

from harness_common import Artifact

from eval_harness.model.sample import MetricResult
from eval_harness.worksheet.worksheet import (
    CellState,
    Provision,
    Row,
    ScoreCell,
    SolveCell,
    Worksheet,
)


def _row_to_dict(r: Row) -> dict:
    return {
        "kind": "row",
        "arm_id": r.arm_id,
        "arm_key": r.arm_key,
        "corpus": r.corpus,
        "case_id": r.case_id,
        "run_id": r.run_id,
        "query": r.query,
        "expected_behavior": r.expected_behavior,
        "ground_truth": r.ground_truth,
        "dimensions": r.dimensions,
        "evidence_sources": r.evidence_sources,
        "candidate_sources": r.candidate_sources,
        "solve": {
            "state": r.solve.state.value,
            "response": r.solve.response,
            "retrieved": r.solve.retrieved,
            "citations": r.solve.citations,
            "observations": r.solve.observations,
            "error": r.solve.error,
        },
        "scores": {
            name: {
                "state": cell.state.value,
                "result": cell.result.to_dict() if cell.result else None,
                "error": cell.error,
            }
            for name, cell in r.scores.items()
        },
        "meta": r.meta,
    }


def _row_from_dict(d: dict) -> Row:
    s = d["solve"]
    solve = SolveCell(
        state=CellState(s["state"]),
        response=s.get("response"),
        retrieved=list(s.get("retrieved") or []),
        citations=list(s.get("citations") or []),
        observations=dict(s.get("observations") or {}),
        error=s.get("error"),
    )
    scores: dict[str, ScoreCell] = {}
    for name, c in (d.get("scores") or {}).items():
        result = MetricResult.from_dict(c["result"]) if c.get("result") else None
        scores[name] = ScoreCell(state=CellState(c["state"]), result=result, error=c.get("error"))
    return Row(
        arm_id=d["arm_id"],
        arm_key=d["arm_key"],
        corpus=d.get("corpus", ""),
        case_id=d["case_id"],
        query=d["query"],
        run_id=d.get("run_id", ""),
        expected_behavior=d.get("expected_behavior", "answer"),
        ground_truth=d.get("ground_truth"),
        dimensions=dict(d.get("dimensions") or {}),
        evidence_sources=list(d.get("evidence_sources") or []),
        candidate_sources=list(d.get("candidate_sources") or []),
        solve=solve,
        scores=scores,
        meta=dict(d.get("meta") or {}),
    )


def to_jsonl(ws: Worksheet, path: str | Path) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    lines: list[str] = []
    meta = {
        "kind": "meta",
        "experiment": ws.experiment,
        "experiment_hash": ws.experiment_hash,
        "run_id": ws.run_id,
        "created_at": ws.created_at,
        "artifacts": [{"name": artifact.name, "path": artifact.path} for artifact in ws.artifacts],
        "metric_names": ws.metric_names,
        "provisions": [
            {"key": p.key, "state": p.state.value, "subject_id": p.subject_id, "error": p.error}
            for p in ws.provisions.values()
        ],
    }
    lines.append(json.dumps(meta, ensure_ascii=False))
    lines.extend(json.dumps(_row_to_dict(r), ensure_ascii=False) for r in ws.rows.values())
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text("\n".join(lines) + "\n", encoding="utf-8")
    os.replace(tmp, path)  # atomic


def from_jsonl(path: str | Path) -> Worksheet:
    path = Path(path)
    meta: dict | None = None
    rows: dict[tuple[str, str, str], Row] = {}
    with path.open(encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            rec = json.loads(line)
            if rec.get("kind") == "meta":
                meta = rec
            else:
                row = _row_from_dict(rec)
                rows[(row.arm_id, row.corpus, row.case_id)] = row
    if meta is None:
        raise ValueError(f"checkpoint missing meta line: {path}")
    provisions = {
        p["key"]: Provision(
            key=p["key"],
            state=CellState(p["state"]),
            subject_id=p.get("subject_id"),
            error=p.get("error"),
        )
        for p in meta.get("provisions", [])
    }
    ws = Worksheet(
        experiment=meta["experiment"],
        experiment_hash=meta["experiment_hash"],
        metric_names=meta["metric_names"],
        rows=rows,
        provisions=provisions,
        run_id=meta.get("run_id", ""),
        created_at=meta.get("created_at", ""),
    )
    ws.artifacts = [Artifact(**artifact) for artifact in meta.get("artifacts", [])]
    return ws


class Checkpointer:
    """Periodically flush a Worksheet to disk while the reconcile runs.

    Use as ``async with Checkpointer(ws, path, interval_s=10): ...`` — it flushes
    every ``interval_s`` and once more on exit, so the on-disk checkpoint is at
    most ``interval_s`` behind the in-memory truth.
    """

    def __init__(self, ws: Worksheet, path: str | Path, interval_s: float = 10.0) -> None:
        self._ws = ws
        self._path = Path(path)
        self._interval = interval_s
        self._task: asyncio.Task | None = None
        self._stop = asyncio.Event()

    def flush(self) -> None:
        to_jsonl(self._ws, self._path)

    async def _loop(self) -> None:
        while not self._stop.is_set():
            with contextlib.suppress(asyncio.TimeoutError):
                await asyncio.wait_for(self._stop.wait(), timeout=self._interval)
            self.flush()

    async def __aenter__(self) -> Checkpointer:
        self._task = asyncio.create_task(self._loop())
        return self

    async def __aexit__(self, *exc) -> None:
        self._stop.set()
        if self._task:
            await self._task
        self.flush()
