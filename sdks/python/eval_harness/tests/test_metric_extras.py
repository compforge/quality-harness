"""DIAGNOSTIC weight resolution + the re-homed LLMJudge base."""

from __future__ import annotations

import asyncio

import pytest

from eval_harness.engine import resolve_weights
from eval_harness.llm import ChatResult
from eval_harness.metric.base import BaseMetric
from eval_harness.metric.llm_judge import LLMJudge
from eval_harness.model.evalset import EvalSet
from eval_harness.model.experiment import Experiment, Service
from eval_harness.model.sample import Sample
from eval_harness.tests.eval_cases import make_eval_case


class _Scored(BaseMetric):
    NAME = "correctness"

    def score(self, s):
        return self.quality(1.0)


class _Diag(BaseMetric):
    NAME = "faith"
    DIAGNOSTIC = True

    def score(self, s):
        return self.quality(1.0)


def _exp(weights=None):
    return Experiment(
        name="e",
        service=Service(name="chat"),
        evalsets=[EvalSet(caseset="c", cases=[make_eval_case(id="q", query="q")])],
        metrics=["correctness", "faith"],
        weights=weights or {},
    )


def test_diagnostic_defaults_to_zero():
    w = resolve_weights(_exp(), [_Scored(), _Diag()])
    assert w["correctness"] == 1.0
    assert w["faith"] == 0.0  # diagnostic excluded without a config entry


def test_diagnostic_overridable():
    w = resolve_weights(_exp({"faith": 0.5}), [_Scored(), _Diag()])
    assert w["faith"] == 0.5  # experiment can still promote it


def test_unknown_weight_key_raises():
    # a weight naming no live metric (typo / stale config) is drift, not a no-op — see common.Overlay
    with pytest.raises(ValueError, match="unknown catalog id"):
        resolve_weights(_exp({"nope": 0.5}), [_Scored(), _Diag()])


# ----- LLMJudge -----


class _StubClient:
    def __init__(self, text, ready=True):
        self._text, self._ready = text, ready

    def ready(self):
        return self._ready

    async def complete(self, system, user):
        return ChatResult(text=self._text, model="stub")


class _Judge(LLMJudge):
    NAME = "j"
    SYSTEM_PROMPT = "judge it"

    def build_prompt(self, sample):
        return sample.query


def _score(client):
    return asyncio.run(_Judge(client=client).score(Sample(case_id="q", arm_id="e", query="x")))


def test_llm_judge_parses_score():
    r = _score(_StubClient('here: {"score": 0.8, "judgement": "ok"}'))
    assert r.score == 0.8 and r.kind == "score" and r.judgement == "ok"


def test_llm_judge_not_ready_abstains():
    assert _score(_StubClient("", ready=False)).score is None


def test_llm_judge_bad_json_abstains():
    assert _score(_StubClient("no json at all")).score is None
