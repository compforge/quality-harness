"""``LLMJudge`` — base for LLM-as-judge quality metrics.

Generic across consumers: correctness / faithfulness / coverage / relevance are all
"render a prompt, ask a model for ``{score, judgement}``". Subclass, set ``NAME`` +
``SYSTEM_PROMPT``, implement ``build_prompt``; this base owns the model call
(``eval_harness.llm.LLMClient``, configured via ``EVAL_JUDGE_*`` env vars) and JSON parse.

Degrades to abstain (``score=None``), never a fake 0: not-applicable,
judge-not-configured, and call/parse errors all return ``na`` so one bad sample or a
missing key never crashes a run or silently scores 0.
"""

from __future__ import annotations

import json
import re
from abc import abstractmethod

from harness_common.llm import LLMClient

from eval_harness.metric.base import BaseMetric
from eval_harness.model.sample import MetricResult, Sample

_JSON_OBJECT = re.compile(r"\{.*\}", re.DOTALL)


def _first_json_object(text: str) -> dict | None:
    """First balanced JSON object in ``text`` (tolerates ```json fences + prose)."""
    match = _JSON_OBJECT.search(text)
    if not match:
        return None
    blob = match.group(0)
    for end in range(len(blob), 0, -1):
        chunk = blob[:end]
        if not chunk.endswith("}"):
            continue
        try:
            parsed = json.loads(chunk)
        except json.JSONDecodeError:
            continue
        if isinstance(parsed, dict):
            return parsed
    return None


class LLMJudge(BaseMetric):
    """Async LLM judge. Subclass + set NAME + SYSTEM_PROMPT + implement build_prompt."""

    SYSTEM_PROMPT: str

    def __init__(self, client: LLMClient | None = None, **kwargs) -> None:
        super().__init__(**kwargs)
        if not getattr(type(self), "SYSTEM_PROMPT", None):
            raise TypeError(f"{type(self).__name__} must set class attribute `SYSTEM_PROMPT`")
        # from_env() never raises on missing config — ready() reports False instead.
        self.client = client or LLMClient.from_env()

    @abstractmethod
    def build_prompt(self, sample: Sample) -> str:
        """Render the user-side prompt for this sample."""

    async def score(self, sample: Sample) -> MetricResult:
        if not self.applies_to(sample):
            return self.na()
        if not self.client.ready():
            return self.na("judge config missing (check EVAL_JUDGE_* env vars)")
        try:
            result = await self.client.complete(self.SYSTEM_PROMPT, self.build_prompt(sample))
            return self._parse(result.text)
        except Exception as exc:  # noqa: BLE001 — surface as abstain, don't crash the run
            return self.na(f"judge error: {exc}")

    def _parse(self, raw: str) -> MetricResult:
        payload = _first_json_object(raw)
        if payload is None:
            return self.na(f"no JSON object in response: {raw[:160]!r}")
        score = payload.get("score")
        judgement = payload.get("judgement") or payload.get("reason") or ""
        if isinstance(score, int | float):
            return self.quality(float(score), str(judgement) or "(no judgement)")
        return self.na(f"score field missing/invalid: {payload!r}")
