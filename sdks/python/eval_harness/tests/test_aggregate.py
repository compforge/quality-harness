from eval_harness.metric.aggregate import percentiles, weighted_overall
from eval_harness.model.sample import MetricResult


def _q(name, score):
    return MetricResult(name, "score", score=score)


def test_weighted_overall_renormalises_over_present():
    results = {"correctness": _q("correctness", 0.8), "citation": _q("citation", 0.4)}
    weights = {"correctness": 0.75, "citation": 0.25}
    # 0.8*0.75 + 0.4*0.25 = 0.6 + 0.1 = 0.7
    assert weighted_overall(results, weights) == 0.7


def test_weighted_overall_drops_na_and_renormalises():
    # correctness abstained (None) → composite is just citation, weight renormalised to itself
    results = {"correctness": _q("correctness", None), "citation": _q("citation", 0.4)}
    weights = {"correctness": 0.75, "citation": 0.25}
    assert weighted_overall(results, weights) == 0.4


def test_weighted_overall_excludes_measurement():
    results = {
        "correctness": _q("correctness", 1.0),
        "latency": MetricResult("latency", "measure", value=1200, unit="ms"),
    }
    assert weighted_overall(results, {"correctness": 1.0, "latency": 1.0}) == 1.0


def test_weighted_overall_empty_is_none():
    assert weighted_overall({}, {}) is None
    assert weighted_overall({"c": _q("c", None)}, {"c": 1.0}) is None


def test_percentiles():
    p = percentiles([100, 200, 300, 400, 500])
    assert p["mean"] == 300 and p["p50"] == 300 and p["n"] == 5
    assert percentiles([]) is None
    assert percentiles([42])["p95"] == 42
