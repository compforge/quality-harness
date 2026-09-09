from perf_harness.drive.load import LoadProfile, Schedule
from perf_harness.drive.workload import MockWorkload, Workload
from perf_harness.engine import Engine, Experiment
from perf_harness.model import Outcome, ResourceProfile, Service, Verdict


def test_base_judge_status_and_exc():
    wl = MockWorkload()
    assert wl.judge(Outcome(status=200, duration_ms=1)).ok
    v503 = wl.judge(Outcome(status=503, duration_ms=1))
    assert (v503.ok, v503.error_kind) == (False, "503")
    vexc = wl.judge(Outcome(status=None, duration_ms=0, meta={"exc": "ReadTimeout"}))
    assert (vexc.ok, vexc.error_kind) == (False, "ReadTimeout")
    vnone = wl.judge(Outcome(status=None, duration_ms=0))
    assert (vnone.ok, vnone.error_kind) == (False, "unknown")


class _SSEChat(Workload):
    """An SSE workload whose judge catches 200-but-failed cases HTTP status can't see."""

    name = "sse-chat"

    async def fire(self, ctx):  # not exercised in unit tests
        return Outcome(status=200, duration_ms=1)

    def judge(self, o: Outcome) -> Verdict:
        base = super().judge(o)
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
    wl = _SSEChat()
    healthy = Outcome(status=200, duration_ms=1, events=5, meta={"saw_done": True})
    assert wl.judge(healthy).ok

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
        v = wl.judge(o)
        assert v.ok is False
        assert v.error_kind == expected_kind


class _Failing(Workload):
    name = "failing"

    async def fire(self, ctx):
        return Outcome(status=500, duration_ms=1.0)  # raw — base judge buckets it as "500"


async def test_engine_buckets_judged_errors():
    exp = Experiment(
        service=Service("x", base_url="http://127.0.0.1:0"),
        workload=_Failing(),
        resources=[ResourceProfile()],
        loads=[LoadProfile(model="closed", schedule=Schedule.ramp_hold(2, 0.0, 0.2))],
    )
    r = (await Engine(exp).run()).trials[0]
    assert r.measurement.request.n > 0
    assert r.measurement.request.error_rate == 1.0
    assert r.measurement.request.error_breakdown.get("500", 0) > 0
