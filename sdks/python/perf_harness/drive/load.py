"""Bounded load plans: independent arrival rate and full-request concurrency."""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Literal


@dataclass(frozen=True)
class Stage:
    duration_s: float
    request_rate: float
    max_inflight: int
    kind: Literal["hold", "ramp", "warmup"] = "hold"
    name: str | None = None

    def __post_init__(self) -> None:
        if not math.isfinite(self.duration_s) or self.duration_s <= 0:
            raise ValueError("stage.duration_s must be finite and > 0")
        if math.isnan(self.request_rate) or self.request_rate < 0:
            raise ValueError("stage.request_rate must be >= 0 or inf")
        if (
            isinstance(self.max_inflight, bool)
            or not isinstance(self.max_inflight, int)
            or self.max_inflight < 1
        ):
            raise ValueError("stage.max_inflight must be an integer >= 1")
        if self.kind not in {"hold", "ramp", "warmup"}:
            raise ValueError("stage.kind must be hold, ramp or warmup")

    @property
    def level(self) -> float:
        return self.max_inflight if math.isinf(self.request_rate) else self.request_rate

    @property
    def label(self) -> str:
        return self.name or f"{self.kind}@{self.level:g}"


@dataclass(frozen=True)
class Warmup:
    """Double the request rate from 1 every step; zero disables warmup."""

    step_s: float = 5.0

    def __post_init__(self) -> None:
        if not math.isfinite(self.step_s) or self.step_s < 0:
            raise ValueError("warmup.step_s must be finite and >= 0")


@dataclass(frozen=True, kw_only=True)
class LoadPlan:
    request_rate: float
    max_inflight: int
    hold_s: float = 60.0
    warmup: Warmup = field(default_factory=Warmup)
    stages: tuple[Stage, ...] = ()
    arrival: Literal["constant", "poisson"] = "constant"
    seed: int = 0
    abort_on_error_rate: float | None = None
    breaker_min_n: int = 20
    cooldown_timeout_s: float = 180.0

    def __post_init__(self) -> None:
        Stage(self.hold_s, self.request_rate, self.max_inflight)
        if self.arrival not in {"constant", "poisson"}:
            raise ValueError("load.arrival must be constant or poisson")
        if not self.stages and self.request_rate <= 0:
            raise ValueError("load.request_rate must be > 0")
        if not math.isfinite(self.cooldown_timeout_s) or self.cooldown_timeout_s < 0:
            raise ValueError("load.cooldown_timeout_s must be finite and >= 0")
        if self.abort_on_error_rate is not None and not 0 < self.abort_on_error_rate <= 1:
            raise ValueError("load.abort_on_error_rate must be in (0, 1]")
        if (
            isinstance(self.breaker_min_n, bool)
            or not isinstance(self.breaker_min_n, int)
            or self.breaker_min_n < 1
        ):
            raise ValueError("load.breaker_min_n must be an integer >= 1")
        if any(math.isinf(s.request_rate) != self.saturated for s in self.stages):
            raise ValueError("stages cannot switch between finite request_rate and inf")

    @property
    def saturated(self) -> bool:
        return math.isinf(self.request_rate)

    @property
    def mode(self) -> str:
        return "concurrency" if self.saturated else "rate"

    @property
    def planned_stages(self) -> tuple[Stage, ...]:
        if self.stages:
            return self.stages
        stages = []
        rate = min(1.0, self.request_rate)
        if not self.saturated and self.warmup.step_s:
            while rate < self.request_rate:
                stages.append(Stage(self.warmup.step_s, rate, self.max_inflight, "warmup"))
                rate = min(rate * 2, self.request_rate)
        stages.append(Stage(self.hold_s, self.request_rate, self.max_inflight))
        return tuple(stages)

    @property
    def duration_s(self) -> float:
        """Planned upper bound; an early inflight limit shortens warmup."""
        return sum(s.duration_s for s in self.planned_stages)

    @property
    def peak_level(self) -> float:
        return max(s.level for s in self.planned_stages)

    @property
    def peak_inflight(self) -> int:
        return max(self.max_inflight, *(s.max_inflight for s in self.planned_stages))

    def target(self, elapsed_s: float) -> tuple[float, int]:
        rate, concurrency = self.request_rate, self.max_inflight
        clock = 0.0
        for stage in self.planned_stages:
            if elapsed_s < clock + stage.duration_s:
                if stage.kind != "ramp":
                    return stage.request_rate, stage.max_inflight
                fraction = max(0.0, elapsed_s - clock) / stage.duration_s
                # Infinity is a replenish policy, never an interpolated number.
                r = math.inf if self.saturated else rate + (stage.request_rate - rate) * fraction
                return r, max(
                    1, math.floor(concurrency + (stage.max_inflight - concurrency) * fraction)
                )
            clock += stage.duration_s
            rate, concurrency = stage.request_rate, stage.max_inflight
        return rate, concurrency

    def arrival_time(self, volume: float) -> float:
        """Invert integrated rate, so scheduler stalls never reset the arrival clock."""
        clock, previous = 0.0, self.request_rate
        for stage in self.planned_stages:
            start = previous if stage.kind == "ramp" else stage.request_rate
            slope = (stage.request_rate - start) / stage.duration_s
            mass = (start + stage.request_rate) * stage.duration_s / 2
            if volume < mass:
                if abs(slope) < 1e-12:
                    return clock + volume / start
                root = math.sqrt(max(0.0, start * start + 2 * slope * volume))
                return clock + (2 * volume / (start + root) if volume else 0.0)
            volume -= mass
            clock += stage.duration_s
            previous = stage.request_rate
        return math.inf

    def label(self) -> str:
        return f"{self.mode}/{self.peak_level:g}/c{self.peak_inflight}"
