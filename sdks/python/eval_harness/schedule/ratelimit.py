"""Per-endpoint adaptive rate gate (AIMD) + a registry.

LLM/API rate is the bottom-most concurrency limit, and it differs per endpoint
(the SUT API the solver hits vs the judge LLM the scorer hits, and different
SUT endpoints for different Arms). So gates are **per-endpoint**: each owns a
concurrency limit that additively increases on sustained success and
multiplicatively decreases on error (429 / overload), with a short pause after
an error — saturate when healthy, back off when not.
"""

from __future__ import annotations

import asyncio
import time


class RateGate:
    def __init__(
        self,
        name: str,
        limit: int = 4,
        min_limit: int = 1,
        max_limit: int = 64,
        pause_s: float = 0.5,
    ) -> None:
        self.name = name
        self.limit = limit
        self._min = min_limit
        self._max = max_limit
        self._pause_s = pause_s
        self._inflight = 0
        self._ok_streak = 0
        self._resume_at = 0.0  # monotonic time before which no new acquire proceeds
        self._cond = asyncio.Condition()

    async def acquire(self) -> None:
        async with self._cond:
            while True:
                now = time.monotonic()
                if now < self._resume_at:
                    await asyncio.sleep(self._resume_at - now)
                    continue
                if self._inflight < self.limit:
                    self._inflight += 1
                    return
                await self._cond.wait()

    async def release(self) -> None:
        async with self._cond:
            self._inflight = max(0, self._inflight - 1)
            self._cond.notify_all()

    def on_success(self) -> None:
        # additive increase: bump the limit once per `limit` successes
        self._ok_streak += 1
        if self._ok_streak >= self.limit and self.limit < self._max:
            self.limit += 1
            self._ok_streak = 0

    def on_error(self) -> None:
        # multiplicative decrease + brief pause (circuit cool-down)
        self.limit = max(self._min, self.limit // 2)
        self._ok_streak = 0
        self._resume_at = time.monotonic() + self._pause_s


class GateRegistry:
    """SUT gates keyed by endpoint; one shared judge gate."""

    def __init__(self, sut_limit: int = 4, judge_limit: int = 8, pause_s: float = 0.5) -> None:
        self._sut_limit = sut_limit
        self._pause_s = pause_s
        self._sut: dict[str, RateGate] = {}
        self._judge = RateGate("judge", limit=judge_limit, pause_s=pause_s)

    def sut(self, endpoint: str) -> RateGate:
        if endpoint not in self._sut:
            self._sut[endpoint] = RateGate(
                f"sut:{endpoint}", limit=self._sut_limit, pause_s=self._pause_s
            )
        return self._sut[endpoint]

    def judge(self) -> RateGate:
        return self._judge
