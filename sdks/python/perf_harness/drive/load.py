"""Bounded load plans: independent arrival rate and full-request concurrency."""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Literal


@dataclass(frozen=True)
class Stage:
    duration_s: float
    request_rate: float
    max_concurrency: int
    kind: Literal["hold", "ramp"] = "hold"
    name: str | None = None

    def __post_init__(self) -> None:
        if not math.isfinite(self.duration_s) or self.duration_s <= 0:
            raise ValueError("stage.duration_s must be finite and > 0")
        if math.isnan(self.request_rate) or self.request_rate < 0:
            raise ValueError("stage.request_rate must be >= 0 or inf")
        if (
            isinstance(self.max_concurrency, bool)
            or not isinstance(self.max_concurrency, int)
            or self.max_concurrency < 1
        ):
            raise ValueError("stage.max_concurrency must be an integer >= 1")
        if self.kind not in {"hold", "ramp"}:
            raise ValueError("stage.kind must be hold or ramp")

    @property
    def level(self) -> float:
        return self.max_concurrency if math.isinf(self.request_rate) else self.request_rate

    @property
    def label(self) -> str:
        return self.name or f"{self.kind}@{self.level:g}"


@dataclass(frozen=True, kw_only=True)
class LoadPlan:
    request_rate: float
    max_concurrency: int
    duration_s: float
    stages: tuple[Stage, ...] = ()
    arrival: Literal["constant", "poisson"] = "constant"
    seed: int = 0
    warmup_s: float = 0.0
    abort_on_error_rate: float | None = None
    breaker_min_n: int = 20
    drain_timeout_s: float = 30.0

    def __post_init__(self) -> None:
        Stage(self.duration_s, self.request_rate, self.max_concurrency)
        if self.arrival not in {"constant", "poisson"}:
            raise ValueError("load.arrival must be constant or poisson")
        if not math.isfinite(self.warmup_s) or not 0 <= self.warmup_s < self.duration_s:
            raise ValueError("load.warmup_s must be finite and in [0, duration_s)")
        if not math.isfinite(self.drain_timeout_s) or self.drain_timeout_s < 0:
            raise ValueError("load.drain_timeout_s must be finite and >= 0")
        if self.abort_on_error_rate is not None and not 0 < self.abort_on_error_rate <= 1:
            raise ValueError("load.abort_on_error_rate must be in (0, 1]")
        if (
            isinstance(self.breaker_min_n, bool)
            or not isinstance(self.breaker_min_n, int)
            or self.breaker_min_n < 1
        ):
            raise ValueError("load.breaker_min_n must be an integer >= 1")
        if self.stages and not math.isclose(
            sum(s.duration_s for s in self.stages), self.duration_s
        ):
            raise ValueError("stage durations must sum to load.duration_s")
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
        return self.stages or (Stage(self.duration_s, self.request_rate, self.max_concurrency),)

    @property
    def peak_level(self) -> float:
        return max(s.level for s in self.planned_stages)

    @property
    def peak_concurrency(self) -> int:
        return max(self.max_concurrency, *(s.max_concurrency for s in self.planned_stages))

    def target(self, elapsed_s: float) -> tuple[float, int]:
        rate, concurrency = self.request_rate, self.max_concurrency
        clock = 0.0
        for stage in self.planned_stages:
            if elapsed_s < clock + stage.duration_s:
                if stage.kind == "hold":
                    return stage.request_rate, stage.max_concurrency
                fraction = max(0.0, elapsed_s - clock) / stage.duration_s
                # Infinity is a replenish policy, never an interpolated number.
                r = math.inf if self.saturated else rate + (stage.request_rate - rate) * fraction
                return r, max(
                    1, math.floor(concurrency + (stage.max_concurrency - concurrency) * fraction)
                )
            clock += stage.duration_s
            rate, concurrency = stage.request_rate, stage.max_concurrency
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
        return f"{self.mode}/{self.peak_level:g}/c{self.peak_concurrency}"
