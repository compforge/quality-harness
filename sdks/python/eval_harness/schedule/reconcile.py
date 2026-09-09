"""``ReconcileEngine`` — fill the Worksheet's missing cells (缺啥补啥).

Not a linear pipeline: it computes the gaps and fills them, bounded only by
per-endpoint rate gates and cell dependencies (provision → solve → score). Each
row's solve→score chain runs concurrently, so the SUT and judge endpoints stay
busy together. Idempotent: re-running over a reloaded Worksheet skips OK cells
and only fills what is still PENDING/FAILED — that *is* the resume mechanism.

Producers are injected (Protocols) so the engine is testable with mocks and the
a real SUT adapter drops in unchanged.
"""

from __future__ import annotations

import asyncio
import inspect
from abc import ABC, abstractmethod
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Any, Protocol

from eval_harness.metric.base import BaseMetric
from eval_harness.model.evalset import EvalSet
from eval_harness.model.experiment import Experiment, Service
from eval_harness.model.sample import Sample
from eval_harness.schedule.ratelimit import GateRegistry, RateGate
from eval_harness.worksheet.worksheet import CellState, Row, SolveCell, Worksheet


@dataclass
class SolveResult:
    response: str | None = None
    retrieved: list[str] = field(default_factory=list)
    citations: list[dict] = field(default_factory=list)
    observations: dict[str, Any] = field(default_factory=dict)  # ttft_ms / total_ms / tokens
    meta: dict[str, Any] = field(default_factory=dict)  # message_id / trace_id


class Provisioner(Protocol):
    """Owns the heavy provisioned resource (the subject a solve queries).

    ``prepare`` MUST be idempotent. The engine may call it again for the same
    ``key`` — both on resume (a FAILED provision is re-attempted) and, if
    ``provision_attempts > 1``, within a run. Critically, a *partial* prepare
    (e.g. resource created, then ingest fails) leaves side effects behind, so a
    later call must **reuse** the existing resource rather than erroring on a
    name/uniqueness clash. Provisioning is keyed by ``key`` (not the arm_id), so
    same-key arms share one resource — prepare once, not per arm.

    ``prepare`` receives the canonical CaseSet's Eval projection (identity + sources + focus),
    so the provisioner needs no separate manifest of its own.
    """

    async def prepare(self, key: str, service: Service, evalset: EvalSet) -> str: ...
    async def clean(self, key: str, subject_id: str) -> None: ...


class Solver(ABC):
    """Fills the answer-side of a cell. Subclass and implement ``solve`` (triggers the
    SUT, e.g. a chat).

    Optionally override ``fetch`` to return an already-stored SUT result for this cell
    (keyed by the run's ``row.run_id`` — a user-controlled reuse namespace — plus the row
    fields arm_id / corpus / case_id; how that maps to a SUT key is the solver's business).
    The engine calls ``fetch`` first (ungated) and only ``solve`` (rate-gated) on a miss,
    so re-runs reuse what the SUT already produced. The default returns None (no reuse →
    always trigger).
    """

    @abstractmethod
    async def solve(self, row: Row, service: Service, subject_id: str) -> SolveResult: ...

    async def fetch(self, row: Row, service: Service, subject_id: str) -> SolveResult | None:
        return None


async def _maybe_await(x: Any) -> Any:
    return await x if inspect.isawaitable(x) else x


def _sut_endpoint(service: Service) -> str:
    """Rate-limit bucket key for a SUT call — the run API the solver hits.

    Different models/endpoints are different buckets (independent limits), so model-alpha vs
    model-beta don't throttle each other. Prefer the typed ``llm`` spec (base_url + model);
    fall back to the service's ``name``.
    """
    if service.llm and (service.llm.base_url or service.llm.model):
        return f"{service.llm.base_url or ''}::{service.llm.model or ''}"
    return service.name or "default"


class ReconcileEngine:
    def __init__(
        self,
        ws: Worksheet,
        exp: Experiment,
        provisioner: Provisioner,
        solver: Solver,
        metrics: list[BaseMetric],
        gates: GateRegistry | None = None,
        *,
        max_attempts: int = 3,
        provision_attempts: int = 1,
        backoff_base: float = 0.0,
    ) -> None:
        self.ws = ws
        self.exp = exp
        self.provisioner = provisioner
        self.solver = solver
        self.metric_by_name = {m.NAME: m for m in metrics}
        self.gates = gates or GateRegistry()
        self.max_attempts = max_attempts
        # prepare is heavy + side-effecting; default to a single in-run attempt so a
        # non-idempotent failure surfaces its real error (instead of being masked by a
        # retry that clashes with the partial resource). Retries still happen on resume.
        self.provision_attempts = provision_attempts
        self.backoff_base = backoff_base
        # Arm id → resolved service; provision key → resolved service + its EvalSet.
        # An experiment may span corpora, so a key is (Arm-heavy × corpus); each key
        # provisions its own corpus's resource.
        self._target_by_arm: dict[str, Service] = {}
        self._target_by_key: dict[str, Service] = {}
        self._evalset_by_key: dict[str, EvalSet] = {}
        for arm in exp.resolved_arms():
            rt = arm.resolve(exp.service)
            self._target_by_arm[arm.id] = rt
            for es in exp.evalsets:
                key = arm.key(es.corpus, exp.service, exp.heavy_fields)
                self._target_by_key.setdefault(key, rt)
                self._evalset_by_key.setdefault(key, es)

    async def run(self) -> None:
        if self.ws.experiment_hash != self.exp.experiment_hash():
            raise RuntimeError(
                "experiment_hash mismatch with checkpoint; rerun fresh "
                "(do not silently resume an edited experiment)"
            )
        await self._provision_all()
        await asyncio.gather(*(self._row_chain(r) for r in self.ws.rows.values()))

    # ----- stages -----

    async def _provision_all(self) -> None:
        pending = [p.key for p in self.ws.provisions_pending()]
        await asyncio.gather(*(self._provision(k) for k in pending))

    async def _provision(self, key: str) -> None:
        service = self._target_by_key[key]
        gate = self.gates.sut(_sut_endpoint(service))
        try:
            sid = await self._attempt(
                gate,
                lambda: self.provisioner.prepare(key, service, self._evalset_by_key[key]),
                attempts=self.provision_attempts,
            )
            self.ws.set_provision_ok(key, sid)
        except Exception as exc:  # noqa: BLE001 — isolate; FAILED cells retry on resume
            self.ws.set_provision_failed(key, str(exc))

    async def _row_chain(self, row: Row) -> None:
        prov = self.ws.provisions.get(row.arm_key)
        if not prov or prov.state is not CellState.OK:
            return  # provision unavailable → cannot solve this row (left PENDING/whatever)
        if row.solve.state is not CellState.OK:
            service = self._target_by_arm[row.arm_id]
            try:
                res = await self._fetch_or_solve(row, service, prov.subject_id)
                row.solve = SolveCell(
                    state=CellState.OK,
                    response=res.response,
                    retrieved=list(res.retrieved),
                    citations=list(res.citations),
                    observations=dict(res.observations),
                )
                row.meta.update(res.meta)
            except Exception as exc:  # noqa: BLE001
                row.solve = SolveCell(state=CellState.FAILED, error=str(exc))
                return
        sample = row.to_sample()
        todo = [
            name
            for name in self.ws.metric_names
            if (cell := row.scores.get(name)) is None or cell.state is not CellState.OK
        ]
        await asyncio.gather(*(self._score_one(row, sample, name) for name in todo))

    async def _fetch_or_solve(self, row: Row, service: Service, subject_id: str) -> SolveResult:
        """Reuse an already-stored SUT result if ``fetch`` finds one (cheap, ungated);
        otherwise trigger ``solve`` under the SUT rate gate. The default ``fetch`` returns
        None, so a solver that doesn't override it always triggers."""
        hit = await self.solver.fetch(row, service, subject_id)
        if hit is not None:
            return hit
        gate = self.gates.sut(_sut_endpoint(service))
        return await self._attempt(gate, lambda: self.solver.solve(row, service, subject_id))

    async def _score_one(self, row: Row, sample: Sample, name: str) -> None:
        metric = self.metric_by_name.get(name)
        if metric is None:
            self.ws.set_score_failed(row, name, "unknown metric")
            return
        if not metric.applies_to(sample):
            self.ws.set_score(row, name, metric.na())  # abstain, no call
            return
        gate = None if metric.KIND == "measure" else self.gates.judge()  # measure reads obs, no LLM
        try:
            result = await self._attempt(gate, lambda: _maybe_await(metric.score(sample)))
            self.ws.set_score(row, name, result)
        except Exception as exc:  # noqa: BLE001
            self.ws.set_score_failed(row, name, str(exc))

    # ----- retry under a gate (AIMD recorded per attempt) -----

    async def _attempt(
        self,
        gate: RateGate | None,
        fn: Callable[[], Awaitable[Any]],
        *,
        attempts: int | None = None,
    ) -> Any:
        n = attempts if attempts is not None else self.max_attempts
        last: Exception | None = None
        for i in range(n):
            if gate:
                await gate.acquire()
            try:
                r = await fn()
                if gate:
                    gate.on_success()
                return r
            except Exception as exc:  # noqa: BLE001
                last = exc
                if gate:
                    gate.on_error()
                if i + 1 < n and self.backoff_base:
                    await asyncio.sleep(self.backoff_base * (2**i))
            finally:
                if gate:
                    await gate.release()
        assert last is not None
        raise last
