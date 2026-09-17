from harness_common.overlay import Overlay
from spec_case.model import Case

from perf_harness.drive.load import LoadPlan, Warmup
from perf_harness.drive.runner import MockRunner, Runner
from perf_harness.engine import Engine, Experiment
from perf_harness.model import Outcome, ResourceProfile, Service


def _subject() -> Service:
    return Service("mock", base_url="http://127.0.0.1:0")


async def test_facets_pivot_and_weighting():
    experiment = Experiment(
        service=_subject(),
        runner=MockRunner(),
        resources=[ResourceProfile(workers=2)],
        loads=[
            LoadPlan(
                warmup=Warmup(step_s=0),
                request_rate=float("inf"),
                max_inflight=4,
                hold_s=(0.0 + 0.5),
            )
        ],
        cases=[
            Case(id="simple", input={"ms": 2}, facets={"difficulty": "simple"}),
            Case(id="complex", input={"ms": 20}, facets={"difficulty": "complex"}),
        ],
        mix=Overlay({"simple": 80, "complex": 20}),
    )
    r = (await Engine(experiment).run()).arm_runs[0]

    assert r.measurement.request.n > 0
    assert set(r.measurement.by_case) == {"simple", "complex"}
    assert sum(s.n for s in r.measurement.by_case.values()) == r.measurement.request.n
    assert "difficulty" in r.measurement.by_facet
    by = r.measurement.by_facet["difficulty"]
    assert set(by) <= {"simple", "complex"}
    # every outcome is tagged → the pivot partitions the whole arm_run
    assert sum(s.n for s in by.values()) == r.measurement.request.n
    # both present with enough fires; weight 80/20 → more simple; ms 20 > 2 → higher p50
    assert "simple" in by and "complex" in by
    assert by["simple"].n > by["complex"].n
    assert by["complex"].p50_ms > by["simple"].p50_ms


class _SlowRunner(Runner):
    name = "slow"

    async def fire(self, ctx):
        import asyncio

        await asyncio.sleep(0.05)
        return Outcome(status=200, duration_ms=50)


async def test_inflight_backpressure_preserves_case_and_facet_latency_samples():
    # A 50ms request with two slots cannot sustain 200 RPS. Pausing the source
    # must preserve the latency of real calls and their case/facet attribution.
    experiment = Experiment(
        service=_subject(),
        runner=_SlowRunner(),
        resources=[ResourceProfile(workers=2)],
        loads=[
            LoadPlan(warmup=Warmup(step_s=0), request_rate=200, max_inflight=2, hold_s=(0.0 + 0.4))
        ],
        cases=[Case(id="x", input={}, facets={"difficulty": "simple"})],
    )
    r = (await Engine(experiment).run()).arm_runs[0]
    assert r.measurement.request.n_dropped == 0
    assert r.measurement.limited_s > 0
    assert "co_biased" in r.measurement.request.caveats
    assert r.measurement.request.n > 0  # some requests were actually sent
    assert set(r.measurement.by_case) == {"x"}
    assert r.measurement.by_case["x"].n == r.measurement.request.n
    assert r.measurement.by_case["x"].n_dropped == r.measurement.request.n_dropped
    # Only actual 50ms requests contribute to the latency distribution.
    assert r.measurement.request.p50_ms >= 40
    # Intentional backpressure is not a request error.
    assert "client_saturated" not in r.measurement.request.error_breakdown
    # Case and facet projections reconcile with the same request facts.
    assert "difficulty" in r.measurement.by_facet
    assert (
        sum(s.n for s in r.measurement.by_facet["difficulty"].values()) == r.measurement.request.n
    )
    assert (
        sum(s.n_dropped for s in r.measurement.by_facet["difficulty"].values())
        == r.measurement.request.n_dropped
    )


async def test_no_cases_is_anonymous_no_facets():
    experiment = Experiment(
        service=_subject(),
        runner=MockRunner(base_ms=2),
        resources=[ResourceProfile(workers=2)],
        loads=[
            LoadPlan(
                warmup=Warmup(step_s=0),
                request_rate=float("inf"),
                max_inflight=2,
                hold_s=(0.0 + 0.2),
            )
        ],
    )
    r = (await Engine(experiment).run()).arm_runs[0]
    assert r.measurement.request.n > 0
    assert r.measurement.by_case == {"default": r.measurement.request}
    assert r.measurement.by_facet == {}
