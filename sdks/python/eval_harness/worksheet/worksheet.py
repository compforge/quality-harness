"""``Worksheet`` / ``Row`` / cells — the in-memory truth, with per-cell state.

Cell state (PENDING / OK / FAILED) is what makes resume uniform: there are no
prepare/run/eval stage barriers, just cells; the reconciler fills any cell whose
dependencies are met and that is not yet OK. A crashed-then-reloaded Worksheet
resumes by filling whatever is still PENDING/FAILED — at whatever granularity
(provision / solve / a single metric) the gap happens to be.

``provisions`` (Arm.key → Provision) is the shared heavy layer: many Arms may
map to one key and share its provisioned resource. Kept in the
Worksheet so a single checkpoint captures the whole resumable state.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Any

from harness_common import ExperimentRun

from eval_harness.model.evalset import eval_view
from eval_harness.model.experiment import Experiment
from eval_harness.model.sample import MetricResult, Sample


class CellState(str, Enum):
    PENDING = "pending"
    OK = "ok"
    FAILED = "failed"


@dataclass
class Provision:
    """Heavy provisioned resource for one Arm.key (shared across same-key Arms)."""

    key: str
    state: CellState = CellState.PENDING
    subject_id: str | None = None  # e.g. a provisioned resource id
    error: str | None = None


@dataclass
class SolveCell:
    state: CellState = CellState.PENDING
    response: str | None = None
    retrieved: list[str] = field(default_factory=list)
    citations: list[dict] = field(default_factory=list)
    observations: dict[str, Any] = field(default_factory=dict)  # ttft_ms / total_ms / tokens
    error: str | None = None


@dataclass
class ScoreCell:
    state: CellState = CellState.PENDING
    result: MetricResult | None = None
    error: str | None = None


@dataclass
class Row:
    arm_id: str
    arm_key: str
    corpus: str
    case_id: str
    # seed (from evalset; never filled by solve/score)
    query: str
    # the run's reuse namespace (a solver keys SUT lookups by it + arm_id/corpus/case)
    run_id: str = ""
    expected_behavior: str = "answer"
    ground_truth: str | None = None
    dimensions: dict[str, str] = field(default_factory=dict)
    evidence_sources: list[str] = field(default_factory=list)
    # candidate source set this case retrieves over (empty = whole subject); the solver
    # narrows the call to these (per-query retrieval pools). Seeded from a case's
    # `input.candidate_sources` via `eval_view`.
    candidate_sources: list[str] = field(default_factory=list)
    # outputs
    solve: SolveCell = field(default_factory=SolveCell)
    scores: dict[str, ScoreCell] = field(default_factory=dict)
    # provenance
    meta: dict[str, Any] = field(default_factory=dict)

    def to_sample(self) -> Sample:
        return Sample(
            case_id=self.case_id,
            arm_id=self.arm_id,
            corpus=self.corpus,
            query=self.query,
            expected_behavior=self.expected_behavior,
            response=self.solve.response,
            ground_truth=self.ground_truth,
            retrieved=tuple(self.solve.retrieved),
            citations=tuple(self.solve.citations),
            evidence_sources=tuple(self.evidence_sources),
            candidate_sources=tuple(self.candidate_sources),
            dimensions=dict(self.dimensions),
            observations=dict(self.solve.observations),
        )


class Worksheet(ExperimentRun):
    def __init__(
        self,
        experiment: str,
        experiment_hash: str,
        metric_names: list[str],
        rows: dict[tuple[str, str, str], Row] | None = None,
        provisions: dict[str, Provision] | None = None,
        run_id: str = "",
        created_at: str = "",
    ) -> None:
        super().__init__(
            run_id=run_id,
            experiment=experiment,
            created_at=created_at,
        )
        self.experiment_hash = experiment_hash
        self.metric_names = list(metric_names)
        # keyed by (arm_id, corpus, case_id) — corpus in the key so an experiment can span
        # corpora without case_id collisions across corpora.
        self.rows: dict[tuple[str, str, str], Row] = rows or {}
        self.provisions: dict[str, Provision] = provisions or {}

    # ----- construction -----

    @classmethod
    def build(cls, exp: Experiment, run_id: str = "") -> Worksheet:
        """Materialise all (arm_id × corpus × case) rows in PENDING state.

        Provisioning is keyed by ``Arm.key`` (which already folds in corpus), so each
        distinct corpus becomes its own provisioned resource and same-key arms still
        share one — many corpora, one worksheet. ``run_id`` is this run's reuse
        namespace, stamped onto every row.
        """
        service = exp.service
        ws = cls(
            exp.name,
            exp.experiment_hash(),
            list(exp.metrics),
            run_id=run_id,
            created_at=datetime.now().astimezone().isoformat(),
        )
        for arm in exp.resolved_arms():
            for corpus, case in exp.cases():
                key = arm.key(corpus, service, exp.heavy_fields)
                ws.provisions.setdefault(key, Provision(key=key))
                v = eval_view(case)  # eval's read of the canonical case → Row seed fields
                row = Row(
                    arm_id=arm.id,
                    arm_key=key,
                    corpus=corpus,
                    case_id=case.id,
                    query=v.query,
                    run_id=run_id,
                    expected_behavior=v.expected_behavior,
                    ground_truth=v.ground_truth,
                    dimensions=dict(v.dimensions),
                    evidence_sources=list(v.evidence_sources),
                    candidate_sources=list(v.candidate_sources),
                    scores={m: ScoreCell() for m in exp.metrics},
                )
                ws.rows[(arm.id, corpus, case.id)] = row
        return ws

    # ----- queries (what the reconciler needs) -----

    def provisions_pending(self) -> list[Provision]:
        return [
            p for p in self.provisions.values() if p.state in (CellState.PENDING, CellState.FAILED)
        ]

    def rows_needing_solve(self) -> list[Row]:
        """Rows whose provision is OK but solve is not done."""
        out = []
        for row in self.rows.values():
            prov = self.provisions.get(row.arm_key)
            if (
                prov
                and prov.state is CellState.OK
                and row.solve.state in (CellState.PENDING, CellState.FAILED)
            ):
                out.append(row)
        return out

    def scores_needing_fill(self) -> list[tuple[Row, str]]:
        """(row, metric_name) whose solve is OK but the metric cell is not done."""
        out = []
        for row in self.rows.values():
            if row.solve.state is not CellState.OK:
                continue
            for name in self.metric_names:
                cell = row.scores.setdefault(name, ScoreCell())
                if cell.state in (CellState.PENDING, CellState.FAILED):
                    out.append((row, name))
        return out

    def is_complete(self) -> bool:
        return not (
            self.provisions_pending() or self.rows_needing_solve() or self.scores_needing_fill()
        )

    # ----- mutation (producers report results here) -----

    def set_provision_ok(self, key: str, subject_id: str) -> None:
        p = self.provisions[key]
        p.state, p.subject_id, p.error = CellState.OK, subject_id, None

    def set_provision_failed(self, key: str, error: str) -> None:
        p = self.provisions[key]
        p.state, p.error = CellState.FAILED, error

    def set_score(self, row: Row, name: str, result: MetricResult) -> None:
        row.scores[name] = ScoreCell(state=CellState.OK, result=result)

    def set_score_failed(self, row: Row, name: str, error: str) -> None:
        row.scores[name] = ScoreCell(state=CellState.FAILED, error=error)

    # ----- counts (for reports / progress) -----

    def stats(self) -> dict[str, int]:
        rows = list(self.rows.values())
        solved = sum(1 for r in rows if r.solve.state is CellState.OK)
        return {
            "rows": len(rows),
            "provisioned": sum(1 for p in self.provisions.values() if p.state is CellState.OK),
            "solved": solved,
            "failed_solve": sum(1 for r in rows if r.solve.state is CellState.FAILED),
        }
