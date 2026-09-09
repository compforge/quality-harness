"""Load specification — how hard to push, over time.

Three orthogonal axes, kept apart on purpose (they were one tangled scalar
before, which is why ramp was an afterthought):

  - **Model** (``open`` / ``closed``): does the arrival rate depend on response
    time? ``open`` = exogenous arrival rate λ(t) (answers "stable N qps?");
    ``closed`` = N concurrent virtual users each looping fire→think→fire
    (answers "N concurrent users?").
  - **Schedule**: intensity as a piecewise-linear function of time — this is
    what makes *ramp* a first-class concept rather than a bolted-on warmup. The
    intensity unit follows the Model: open→arrival rps, closed→concurrent users.
  - **Pacing** (closed only): per-user think-time between successive fires.

The *what to fire* is a ``Case`` (model.py); the *how to fire it* is a
``Workload`` (workload.py). This module is just the *how hard, when*.
"""

from __future__ import annotations

import random
from dataclasses import dataclass, field
from typing import Literal

LoadModel = Literal["open", "closed"]
PacingKind = Literal["none", "constant", "between", "constant_pacing"]


@dataclass(frozen=True)
class Stage:
    """One leg of a Schedule.

    - ``ramp``: intensity moves linearly from the previous stage's end level to
      ``to_level`` over ``over_s`` seconds.
    - ``hold``: intensity stays at ``to_level`` for ``over_s`` seconds.
    """

    over_s: float
    to_level: float
    kind: Literal["ramp", "hold"] = "ramp"
    name: str | None = None  # report label for this stage's slice; auto if unset

    @property
    def label(self) -> str:
        if self.name:
            return self.name
        return f"hold@{self.to_level:g}" if self.kind == "hold" else f"ramp→{self.to_level:g}"


@dataclass(frozen=True)
class Schedule:
    """Intensity over time as a continuous piecewise-linear curve.

    Stages are chained: a ``ramp`` starts wherever the previous stage ended
    (the first stage's ramp starts at ``start_level``); a ``hold`` sits at its
    own ``to_level``. Past the last stage the final level holds. The intensity
    *unit* is decided by the LoadModel (open→rps, closed→users).
    """

    stages: tuple[Stage, ...]
    start_level: float = 0.0

    @property
    def total_s(self) -> float:
        return sum(s.over_s for s in self.stages)

    @property
    def peak_level(self) -> float:
        return max([self.start_level, *(s.to_level for s in self.stages)])

    def intensity(self, t: float) -> float:
        """Target intensity at elapsed ``t`` seconds into the trial.

        A leading ramp enters at ``start_level`` (so ``intensity(0)`` is
        ``start_level``); a leading hold sits at its own ``to_level`` from t=0.
        """
        lvl = self.start_level
        clock = 0.0
        for s in self.stages:
            if t < clock + s.over_s:
                if s.kind == "hold" or s.over_s <= 0:
                    return s.to_level
                # ramp: interpolate from the entering level to to_level
                return lvl + (s.to_level - lvl) * (max(t - clock, 0.0) / s.over_s)
            clock += s.over_s
            lvl = s.to_level
        return lvl  # past the end → hold the final level

    @classmethod
    def ramp_hold(cls, level: float, ramp_s: float, hold_s: float) -> Schedule:
        """The common shape: ramp 0→``level`` over ``ramp_s`` (skipped if 0),
        then hold ``level`` for ``hold_s``."""
        stages: list[Stage] = []
        if ramp_s > 0:
            stages.append(Stage(over_s=ramp_s, to_level=level, kind="ramp"))
        stages.append(Stage(over_s=hold_s, to_level=level, kind="hold"))
        return cls(stages=tuple(stages), start_level=0.0)

    @classmethod
    def spike(
        cls, base: float, peak: float, base_s: float, rise_s: float, peak_s: float
    ) -> Schedule:
        """baseline → fast rise to ``peak`` → short hold → fall back → baseline."""
        return cls(
            stages=(
                Stage(base_s, base, "hold"),
                Stage(rise_s, peak, "ramp"),
                Stage(peak_s, peak, "hold"),
                Stage(rise_s, base, "ramp"),
                Stage(base_s, base, "hold"),
            ),
            start_level=base,
        )


@dataclass(frozen=True)
class Pacing:
    """Closed-loop think-time between a virtual user's successive fires.

    - ``none``: fire back-to-back (max throughput per user).
    - ``constant``: fixed ``secs`` think-time.
    - ``between``: uniform random in ``[secs, max_secs]``.
    - ``constant_pacing``: keep total iteration time at ``secs`` (subtract the
      fire's own duration) → each user paces at ~1/``secs`` req/s.
    """

    kind: PacingKind = "none"
    secs: float = 0.0
    max_secs: float = 0.0

    def wait_s(self, last_fire_s: float) -> float:
        if self.kind == "constant":
            return self.secs
        if self.kind == "between":
            return random.uniform(self.secs, self.max_secs)  # noqa: S311 — load pacing, not crypto
        if self.kind == "constant_pacing":
            return max(0.0, self.secs - last_fire_s)
        return 0.0


@dataclass(frozen=True)
class LoadProfile:
    """How hard to push, over time. The *what* is a Case, the *how* a Workload.

    ``model`` picks the arrival semantics; ``schedule`` shapes intensity over
    time (unit follows model: open→rps, closed→concurrent users); ``pacing`` is
    per-user think-time (closed only); ``warmup_s`` is the leading window the
    aggregate discards before computing steady stats.

    ``max_inflight`` is an open-loop **safety rail** (OOM guard), not a throttle:
    a fire over the cap is *shed* as a ``client_saturated`` drop instead of sent.
    Set it well above the inflight you expect at the target rate — if it engages
    on normal tail events you measure below your intended offered load and the
    latency percentiles understate reality (coordinated omission); the report
    flags any Trial whose drop rate is non-trivial.

    ``abort_on_error_rate`` is a **mid-trial circuit breaker** (distinct from the
    between-trial ``Experiment.abort_on_fail`` SLO gate): once at least
    ``breaker_min_n`` requests have been sent and their cumulative error rate
    (judged failures ÷ sent, INCLUDING the warmup window — it's a safety net, not a
    measurement) reaches this fraction, the driver stops issuing new load, lets
    in-flight requests drain, and ends the trial early (``TrialRecord.stop.early``).
    Use it to avoid hammering a struggling Service (e.g. a dev pod) for the full
    steady window once it's clearly failing. Applies to both open and closed.

    ``graceful_stop_s`` is the wind-down policy used whenever a trial stops (breaker
    OR normal deadline): stop scheduling new fires, then let in-flight requests
    **drain** for up to this many seconds before force-cancelling the stragglers
    (≈ Locust ``stop_timeout`` / k6 ``gracefulStop``). ``0`` = cancel immediately (hard
    stop); a large value = full drain. Cancelled in-flight requests are recorded as
    ``TrialStop.interrupted`` and never enter the latency stats.
    """

    model: LoadModel
    schedule: Schedule
    pacing: Pacing = field(default_factory=Pacing)
    warmup_s: float = 0.0
    max_inflight: int | None = None
    abort_on_error_rate: float | None = None  # mid-trial circuit breaker trip fraction
    breaker_min_n: int = 20  # min sent before the breaker may trip (avoid n=1 → 100%)
    graceful_stop_s: float = 30.0  # drain in-flight up to this long before cancelling

    @property
    def duration_s(self) -> float:
        return self.schedule.total_s

    @property
    def steady_s(self) -> float:
        """Post-warmup window the throughput denominator uses."""
        return max(self.duration_s - self.warmup_s, 1e-9)

    def label(self) -> str:
        unit = "rps" if self.model == "open" else "c"
        return f"{self.model}/{self.schedule.peak_level:g}{unit}"
