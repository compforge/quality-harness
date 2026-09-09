"""Dependency-free builtin metrics (no LLM).

Quality metrics score 0~1; measurement metrics read the solver's raw
observations (latency / tokens) and emit a value+unit. LLM-as-judge metrics
(e.g. semantic correctness) are a separate, optional backend — these builtins
keep the core runnable and testable without any model access.
"""

from __future__ import annotations

from eval_harness.metric.base import BaseMetric
from eval_harness.model.sample import Sample

_REFUSAL_PATTERNS = (
    "无法从",
    "无法回答",
    "未提及",
    "没有提到",
    "文档中并未",
    "无相关",
    "i don't know",
    "cannot find",
    "no information",
    "not enough information",
)


class ExactMatch(BaseMetric):
    """Quality: normalised exact match of response vs ground_truth (answer cases)."""

    NAME = "exact_match"
    KIND = "binary"
    WEIGHT = 1.0
    DESCRIPTION = "答案与标准答案归一化后是否完全一致（1=完全匹配，0=不匹配；仅作答类 case 适用）"

    def applies_to(self, s: Sample) -> bool:
        return bool(s.ground_truth) and s.expected_behavior == "answer"

    def score(self, s: Sample):
        if not self.applies_to(s):
            return self.na()
        a = (s.response or "").strip().casefold()
        b = (s.ground_truth or "").strip().casefold()
        return self.quality(1.0 if a == b else 0.0, "exact" if a == b else "mismatch")


class KeywordRefusal(BaseMetric):
    """Quality: did the model refuse, on a case where it should (no evidence)."""

    NAME = "keyword_refusal"
    KIND = "binary"
    WEIGHT = 1.0
    DESCRIPTION = "在应当拒答的 case 上，模型是否正确拒答（按关键词判定；1=正确拒答，0=未拒答）"

    def applies_to(self, s: Sample) -> bool:
        return s.expected_behavior == "refuse"

    def score(self, s: Sample):
        if not self.applies_to(s):
            return self.na()
        text = (s.response or "").casefold()
        hit = [p for p in _REFUSAL_PATTERNS if p in text]
        return self.quality(
            1.0 if hit else 0.0, f"refused; hit={hit[:2]}" if hit else "did not refuse"
        )


class _Observation(BaseMetric):
    """Base for measurement metrics that just surface a raw observation."""

    KIND = "measure"
    OBS_KEY: str
    UNIT: str

    def score(self, s: Sample):
        v = s.observations.get(self.OBS_KEY)
        return self.measure(float(v), self.UNIT) if v is not None else self.na("not observed")


class Latency(_Observation):
    NAME = "latency"
    OBS_KEY = "total_ms"
    UNIT = "ms"
    DESCRIPTION = "单次请求端到端耗时（ms）；测量类指标，按均值/p95 聚合，不计入加权综合分"


class Ttft(_Observation):
    NAME = "ttft"
    OBS_KEY = "ttft_ms"
    UNIT = "ms"
    DESCRIPTION = "首 token 到达耗时（ms，time-to-first-token）；测量类指标，不计入综合分"


class Tokens(_Observation):
    NAME = "tokens"
    OBS_KEY = "tokens"
    UNIT = "tok"
    DESCRIPTION = "本次回答消耗的 token 数；测量类指标，不计入综合分"
