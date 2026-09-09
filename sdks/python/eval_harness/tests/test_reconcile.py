import pytest

from eval_harness.metric.base import BaseMetric
from eval_harness.model.evalset import EvalSet
from eval_harness.model.experiment import Arm, Experiment, Service
from eval_harness.schedule.ratelimit import GateRegistry
from eval_harness.schedule.reconcile import ReconcileEngine, Solver, SolveResult
from eval_harness.tests.eval_cases import make_eval_case
from eval_harness.worksheet.checkpoint import from_jsonl, to_jsonl
from eval_harness.worksheet.worksheet import CellState, Worksheet

# ----- mock producers -----


class MockProvisioner:
    def __init__(self, fail_keys=frozenset(), fail_times=0):
        self.fail_keys = set(fail_keys)
        self.fail_times = fail_times
        self.attempts: dict[str, int] = {}

    async def prepare(self, key, service, evalset):
        self.attempts[key] = self.attempts.get(key, 0) + 1
        if key in self.fail_keys and self.attempts[key] <= self.fail_times:
            raise RuntimeError("provision boom")
        return f"nb-{key[:6]}"

    async def clean(self, key, subject_id):
        pass


class MockSolver(Solver):
    def __init__(self, fail_cases=frozenset()):
        self.fail_cases = set(fail_cases)
        self.calls = 0

    async def solve(self, row, service, subject_id):
        self.calls += 1
        if row.case_id in self.fail_cases:
            raise RuntimeError("solve boom")
        # echo ground_truth as the answer (so exact-match scores 1.0)
        return SolveResult(
            response=row.ground_truth or "",
            retrieved=["chunk-1"],
            observations={"total_ms": 1000, "ttft_ms": 120},
            meta={"message_id": f"m-{row.arm_id}-{row.case_id}"},
        )


class ExactMatch(BaseMetric):
    NAME = "correctness"
    KIND = "score"
    WEIGHT = 1.0

    def applies_to(self, s):
        return bool(s.ground_truth) and s.expected_behavior == "answer"

    def score(self, s):
        if not self.applies_to(s):
            return self.na()
        ok = (s.response or "").strip() == (s.ground_truth or "").strip()
        return self.quality(1.0 if ok else 0.0, "exact match")


class Latency(BaseMetric):
    NAME = "latency"
    KIND = "measure"

    def score(self, s):
        v = s.observations.get("total_ms")
        return self.measure(v, "ms") if v is not None else self.na()


def _exp(cases=None, arms=None):
    cases = cases or [
        make_eval_case(id="q1", query="q1", ground_truth="a1"),
        make_eval_case(id="q2", query="q2", ground_truth="a2"),
    ]
    return Experiment(
        name="exp",
        service=Service(name="chat", config={"tenant_id": "t1"}),
        evalsets=[EvalSet(caseset="rag", cases=cases)],
        arms=arms or [Arm(id="model-alpha")],
        metrics=["correctness", "latency"],
        weights={"correctness": 1.0},
        heavy_fields=["config.tenant_id"],
    )


def _engine(ws, exp, prov, solver, provision_attempts=1):
    return ReconcileEngine(
        ws,
        exp,
        prov,
        solver,
        [ExactMatch(), Latency()],
        gates=GateRegistry(sut_limit=4, judge_limit=4, pause_s=0.0),
        max_attempts=3,
        provision_attempts=provision_attempts,
        backoff_base=0.0,
    )


async def test_full_fill():
    exp = _exp(
        arms=[
            Arm(id="model-alpha"),
            Arm(id="model-beta", overrides={"config.tenant_id": "t2"}),
        ]
    )
    ws = Worksheet.build(exp)
    await _engine(ws, exp, MockProvisioner(), MockSolver()).run()
    assert ws.is_complete()
    r = ws.rows[("model-alpha", "rag", "q1")]
    assert r.solve.state is CellState.OK and r.solve.response == "a1"
    assert r.scores["correctness"].result.score == 1.0
    assert r.scores["latency"].result.value == 1000
    assert r.meta["message_id"] == "m-model-alpha-q1"


async def test_solve_failure_isolated():
    exp = _exp()
    ws = Worksheet.build(exp)
    await _engine(ws, exp, MockProvisioner(), MockSolver(fail_cases={"q2"})).run()
    assert ws.rows[("model-alpha", "rag", "q1")].solve.state is CellState.OK
    bad = ws.rows[("model-alpha", "rag", "q2")]
    assert bad.solve.state is CellState.FAILED and "boom" in bad.solve.error
    # failed solve → its metric cells never filled (stay pending)
    assert bad.scores["correctness"].state is CellState.PENDING


async def test_retry_then_success():
    # in-run prepare retry only when explicitly opted into (provision_attempts > 1)
    exp = _exp()
    ws = Worksheet.build(exp)
    key = next(iter(ws.provisions))
    prov = MockProvisioner(fail_keys={key}, fail_times=1)  # fail once, then succeed
    await _engine(ws, exp, prov, MockSolver(), provision_attempts=2).run()
    assert ws.provisions[key].state is CellState.OK
    assert prov.attempts[key] == 2
    assert ws.is_complete()


async def test_provision_not_retried_in_run_by_default():
    # prepare is side-effecting → default to a single in-run attempt; a transient failure
    # is marked FAILED with its real error (not masked by a retry that clashes with the
    # partial resource) and retried on resume, not in-run.
    exp = _exp()
    ws = Worksheet.build(exp)
    key = next(iter(ws.provisions))
    prov = MockProvisioner(fail_keys={key}, fail_times=1)
    await _engine(ws, exp, prov, MockSolver()).run()  # provision_attempts defaults to 1
    assert prov.attempts[key] == 1
    assert ws.provisions[key].state is CellState.FAILED
    # resume re-attempts prepare (idempotency contract) and succeeds
    await _engine(ws, exp, prov, MockSolver()).run()
    assert prov.attempts[key] == 2
    assert ws.provisions[key].state is CellState.OK
    assert ws.is_complete()


async def test_applies_to_abstains_without_call():
    exp = _exp(cases=[make_eval_case(id="r1", query="q", expected_behavior="refuse")])
    ws = Worksheet.build(exp)
    await _engine(ws, exp, MockProvisioner(), MockSolver()).run()
    cell = ws.rows[("model-alpha", "rag", "r1")].scores["correctness"]
    assert cell.state is CellState.OK and cell.result.score is None  # abstained, not 0


async def test_hash_mismatch_guard():
    exp = _exp()
    ws = Worksheet.build(exp)
    ws.experiment_hash = "tampered"
    with pytest.raises(RuntimeError, match="hash mismatch"):
        await _engine(ws, exp, MockProvisioner(), MockSolver()).run()


async def test_resume_fills_gaps(tmp_path):
    exp = _exp()
    ws = Worksheet.build(exp)
    key = next(iter(ws.provisions))
    # first run: provision always fails → rows unsolved, experiment incomplete
    await _engine(ws, exp, MockProvisioner(fail_keys={key}, fail_times=99), MockSolver()).run()
    assert not ws.is_complete()
    assert ws.provisions[key].state is CellState.FAILED

    # checkpoint + reload (simulate crash/restart)
    path = tmp_path / "worksheet.jsonl"
    to_jsonl(ws, path)
    ws2 = from_jsonl(path)

    # resume with a healthy provisioner: only the gaps get filled
    solver = MockSolver()
    await _engine(ws2, exp, MockProvisioner(), solver).run()
    assert ws2.is_complete()
    assert solver.calls == 2  # both rows solved exactly once on resume


class FetchSolver(Solver):
    """Overrides fetch → the engine reuses the stored result, never triggers solve."""

    def __init__(self):
        self.solve_calls = 0

    async def solve(self, row, service, subject_id):
        self.solve_calls += 1
        return SolveResult(response="triggered")

    async def fetch(self, row, service, subject_id):
        return SolveResult(response="from-cache", retrieved=["c"], meta={"message_id": "cached"})


async def test_fetch_skips_trigger():
    exp = _exp()
    ws = Worksheet.build(exp)
    solver = FetchSolver()
    await _engine(ws, exp, MockProvisioner(), solver).run()
    assert solver.solve_calls == 0  # fetch hit on every row → solve never called
    assert all(r.solve.response == "from-cache" for r in ws.rows.values())
    assert ws.is_complete()


async def test_prepare_receives_evalset():
    from eval_harness.model.evalset import EvalSet

    exp = _exp()
    ws = Worksheet.build(exp)
    seen: dict = {}

    class CapProv:
        async def prepare(self, key, service, evalset):
            seen["es"] = evalset
            return "nb"

        async def clean(self, key, subject_id):
            pass

    await _engine(ws, exp, CapProv(), MockSolver()).run()
    assert isinstance(seen["es"], EvalSet) and seen["es"].corpus == "rag"


async def test_teardown_cleans_each_provision():
    from eval_harness.engine import teardown_provisions

    exp = _exp()
    ws = Worksheet.build(exp)
    cleaned: list[str] = []

    class CleanProv(MockProvisioner):
        async def clean(self, key, subject_id):
            cleaned.append(key)

    prov = CleanProv()
    await _engine(ws, exp, prov, MockSolver()).run()
    failed = await teardown_provisions(ws, prov)
    assert failed == []
    assert set(cleaned) == set(ws.provisions)  # every OK provision torn down


async def test_teardown_best_effort_returns_failed():
    from eval_harness.engine import teardown_provisions

    exp = _exp()
    ws = Worksheet.build(exp)

    class BadClean(MockProvisioner):
        async def clean(self, key, subject_id):
            raise RuntimeError("clean boom")

    prov = BadClean()
    await _engine(ws, exp, prov, MockSolver()).run()
    failed = await teardown_provisions(ws, prov)  # never raises
    assert set(failed) == set(ws.provisions)
