from perf_harness.drive.load import LoadPlan
from perf_harness.drive.runner import Runner
from perf_harness.engine import Engine, Experiment
from perf_harness.judge import default_judge
from perf_harness.model import Outcome, ResourceProfile, Service
from perf_harness.records import RequestEvaluation as Verdict


def test_base_judge_status_and_exc():
    assert default_judge(Outcome(status=200, duration_ms=1)).ok
    v503 = default_judge(Outcome(status=503, duration_ms=1))
    assert (v503.ok, v503.error_kind) == (False, "503")
    vexc = default_judge(Outcome(status=None, duration_ms=0, meta={"exc": "ReadTimeout"}))
    assert (vexc.ok, vexc.error_kind) == (False, "ReadTimeout")
    vnone = default_judge(Outcome(status=None, duration_ms=0))
    assert (vnone.ok, vnone.error_kind) == (False, "unknown")


def sse_judge(o: Outcome) -> Verdict:
    base = default_judge(o)
    if not base.ok:
        return base  # transport error / non-2xx — base already ruled
    if o.events == 0:
        return Verdict(False, "empty_stream")
    if o.meta.get("error_frames"):
        return Verdict(False, "sse_error_event")
    if not o.meta.get("saw_done"):
        return Verdict(False, "truncated")
    return Verdict(True)


def test_sse_override_catches_200_failures():
    healthy = Outcome(status=200, duration_ms=1, events=5, meta={"saw_done": True})
    assert sse_judge(healthy).ok

    cases = {
        "empty_stream": Outcome(status=200, duration_ms=1, events=0, meta={"saw_done": True}),
        "truncated": Outcome(status=200, duration_ms=1, events=5, meta={"saw_done": False}),
        "sse_error_event": Outcome(
            status=200,
            duration_ms=1,
            events=5,
            meta={"saw_done": True, "error_frames": 1},
        ),
        "ConnectError": Outcome(status=None, duration_ms=0, meta={"exc": "ConnectError"}),
    }
    for expected_kind, o in cases.items():
        v = sse_judge(o)
        assert v.ok is False
        assert v.error_kind == expected_kind


class _Failing(Runner):
    name = "failing"

    async def fire(self, ctx):
        return Outcome(status=500, duration_ms=1.0)  # raw — base judge buckets it as "500"


async def test_engine_buckets_judged_errors():
    exp = Experiment(
        service=Service("x", base_url="http://127.0.0.1:0"),
        runner=_Failing(),
        resources=[ResourceProfile()],
        loads=[LoadPlan(request_rate=float("inf"), max_concurrency=2, duration_s=(0.0 + 0.2))],
    )
    r = (await Engine(exp).run()).arm_runs[0]
    assert r.measurement.request.n > 0
    assert r.measurement.request.error_rate == 1.0
    assert r.measurement.request.error_breakdown.get("500", 0) > 0
