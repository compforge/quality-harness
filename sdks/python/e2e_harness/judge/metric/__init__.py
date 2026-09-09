"""Metric primitives — the soft-scoring side of the judge layer (api-mode).

``BaseMetric[Outcome]`` is the abstraction: it reads an ``Outcome`` (latency /
status / event count) and produces a ``MetricResult``; ``KIND = "binary" |
"score"`` controls report rendering (yes/no vs decimal).

LLM-as-judge scoring of non-deterministic answers lives in the sibling
``eval_harness`` package.
"""

from e2e_harness.judge.metric.base import BaseMetric as BaseMetric
from e2e_harness.judge.metric.base import MetricKind as MetricKind
from e2e_harness.judge.metric.base import MetricResult as MetricResult
from e2e_harness.judge.metric.outcome import EventCountMetric as EventCountMetric
from e2e_harness.judge.metric.outcome import LatencyMetric as LatencyMetric
from e2e_harness.judge.metric.outcome import StatusMetric as StatusMetric
from e2e_harness.judge.metric.outcome import score_outcome as score_outcome
from e2e_harness.judge.metric.registry import MetricRegistry as MetricRegistry
