"""Actual stage boundaries for bounded, paced load; independent of async runtimes."""

from __future__ import annotations

import math

from perf_harness.drive.load import LoadPlan
from perf_harness.model import Window


class LoadController:
    def __init__(self, load: LoadPlan):
        self.load = load
        self.stages = list(load.planned_stages)
        self.index = 0
        self.start_s = 0.0
        self.previous = (load.request_rate, load.max_inflight)
        self.windows: list[Window] = []
        self.hold_start_s: float | None = None
        self.end_s: float | None = None
        self._last_s = 0.0
        self._limited = False
        self._open()

    @property
    def done(self) -> bool:
        return self.end_s is not None

    @property
    def phase(self) -> str:
        if self.done:
            return "cooldown"
        return "hold" if self.stages[self.index].kind == "hold" else "warmup"

    @property
    def deadline(self) -> float:
        return self.start_s + self.stages[self.index].duration_s

    def _open(self) -> None:
        stage = self.stages[self.index]
        self.windows.append(
            Window(
                id=f"stage-{self.index}",
                name=stage.label,
                kind=stage.kind,
                start_s=self.start_s,
                end_s=self.start_s,
                complete=False,
                target_level=stage.level,
            )
        )
        if stage.kind == "hold" and self.hold_start_s is None:
            self.hold_start_s = self.start_s

    def target(self, at_s: float) -> tuple[float, int]:
        stage = self.stages[self.index]
        if stage.kind != "ramp":
            return stage.request_rate, stage.max_inflight
        fraction = min(1.0, max(0.0, at_s - self.start_s) / stage.duration_s)
        rate, cap = self.previous
        return (
            math.inf if self.load.saturated else rate + (stage.request_rate - rate) * fraction,
            max(1, math.floor(cap + (stage.max_inflight - cap) * fraction)),
        )

    def advance(self, at_s: float, inflight: int, *, limited: bool = False) -> None:
        while not self.done:
            stage = self.stages[self.index]
            end = min(at_s, self.deadline)
            if self._limited:
                self.windows[-1].limited_s += max(0.0, end - self._last_s)
            self._last_s = end
            self.windows[-1].end_s = end
            # spec: cap feedback can shorten warmup, never the requested hold duration.
            capped = (
                at_s < self.deadline and stage.kind == "warmup" and inflight >= stage.max_inflight
            )
            if at_s < self.deadline and not capped:
                self._limited = limited
                return
            next_index = self.index + 1
            if capped:
                next_index = next(
                    (
                        i
                        for i in range(next_index, len(self.stages))
                        if self.stages[i].kind == "hold"
                    ),
                    len(self.stages),
                )
            window = self.windows[-1]
            window.complete = True
            window.end_reason = (
                "inflight_limit"
                if capped
                else (
                    "target_rate"
                    if stage.kind == "warmup"
                    and next_index < len(self.stages)
                    and self.stages[next_index].kind == "hold"
                    else "duration"
                )
            )
            self.previous = stage.request_rate, stage.max_inflight
            self.start_s = end
            if next_index == len(self.stages):
                self.end_s = end
                return
            self.index = next_index
            self._open()
            self._limited = limited

    def abort(self, at_s: float) -> None:
        if not self.done:
            self.windows[-1].end_s = min(at_s, self.deadline)
            self.windows[-1].end_reason = "aborted"
            self.end_s = self.windows[-1].end_s
