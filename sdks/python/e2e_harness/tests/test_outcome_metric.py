"""Tests for OutcomeMetric base + built-ins (latency, status, event_count)."""

from __future__ import annotations

import pytest

from e2e_harness.judge.metric import (
    EventCountMetric,
    LatencyMetric,
    StatusMetric,
    score_outcome,
)
from e2e_harness.runner.base import Outcome


def _outcome(
    *, status: int = 200, duration_ms: int = 100, events: list | None = None
) -> Outcome:
    metadata = {"events": events or [], "event_count": len(events) if events else 0}
    return Outcome(
        status_code=status, body=None, duration_ms=duration_ms, metadata=metadata
    )


class TestLatencyMetric:
    def test_under_target_is_full_score(self):
        m = LatencyMetric(target_ms=2000)
        r = m.score(_outcome(duration_ms=500))
        assert r.score == 1.0
        assert r.name == "latency"

    def test_at_target_is_full_score(self):
        m = LatencyMetric(target_ms=2000)
        r = m.score(_outcome(duration_ms=2000))
        assert r.score == 1.0

    def test_at_cutoff_is_zero(self):
        m = LatencyMetric(target_ms=1000, cutoff_ms=5000)
        r = m.score(_outcome(duration_ms=5000))
        assert r.score == 0.0

    def test_over_cutoff_is_zero(self):
        m = LatencyMetric(target_ms=1000, cutoff_ms=5000)
        r = m.score(_outcome(duration_ms=10000))
        assert r.score == 0.0

    def test_linear_decay_midpoint(self):
        m = LatencyMetric(target_ms=1000, cutoff_ms=5000)
        # midpoint between target (1000) and cutoff (5000) is 3000
        r = m.score(_outcome(duration_ms=3000))
        assert r.score == 0.5

    def test_default_cutoff_is_5x_target(self):
        m = LatencyMetric(target_ms=1000)  # cutoff defaults to 5000
        assert m.score(_outcome(duration_ms=5000)).score == 0.0

    def test_invalid_target_rejected(self):
        with pytest.raises(ValueError):
            LatencyMetric(target_ms=0)

    def test_invalid_cutoff_rejected(self):
        with pytest.raises(ValueError):
            LatencyMetric(target_ms=1000, cutoff_ms=500)

    def test_custom_name(self):
        m = LatencyMetric(target_ms=1000, name="response_time")
        assert m.score(_outcome()).name == "response_time"


class TestStatusMetric:
    def test_exact_match_pass(self):
        assert StatusMetric(expected=200).score(_outcome(status=200)).score == 1.0

    def test_exact_match_fail(self):
        assert StatusMetric(expected=200).score(_outcome(status=500)).score == 0.0

    def test_range_2xx_pass(self):
        m = StatusMetric(expected=(200, 300))
        assert m.score(_outcome(status=204)).score == 1.0

    def test_range_exclusive_upper(self):
        m = StatusMetric(expected=(200, 300))
        assert m.score(_outcome(status=300)).score == 0.0

    def test_judgement_explains(self):
        r = StatusMetric(expected=200).score(_outcome(status=503))
        assert "503" in r.judgement and "200" in r.judgement


class TestEventCountMetric:
    def test_meets_minimum(self):
        m = EventCountMetric(min_count=3)
        outcome = _outcome(events=[{}, {}, {}, {}])
        assert m.score(outcome).score == 1.0

    def test_below_minimum(self):
        m = EventCountMetric(min_count=5)
        outcome = _outcome(events=[{}, {}])
        assert m.score(outcome).score == 0.0

    def test_missing_metadata(self):
        m = EventCountMetric(min_count=1)
        outcome = Outcome(status_code=200, metadata={})
        r = m.score(outcome)
        # 0 < 1 → score 0; not None (count defaults to 0)
        assert r.score == 0.0

    def test_invalid_min_rejected(self):
        with pytest.raises(ValueError):
            EventCountMetric(min_count=-1)


class TestScoreOutcome:
    def test_runs_all_metrics_in_order(self):
        results = score_outcome(
            _outcome(status=200, duration_ms=500),
            [
                LatencyMetric(target_ms=1000),
                StatusMetric(expected=200),
            ],
        )
        assert len(results) == 2
        assert results[0].name == "latency" and results[0].score == 1.0
        assert results[1].name == "status" and results[1].score == 1.0


class TestMetricKind:
    """StatusMetric / EventCountMetric mark themselves as binary; LatencyMetric stays score."""

    def test_status_metric_is_binary(self):
        assert StatusMetric().score(_outcome()).kind == "binary"

    def test_event_count_metric_is_binary(self):
        assert (
            EventCountMetric(min_count=1).score(_outcome(events=[{}])).kind == "binary"
        )

    def test_latency_metric_is_score(self):
        assert LatencyMetric(target_ms=1000).score(_outcome()).kind == "score"
