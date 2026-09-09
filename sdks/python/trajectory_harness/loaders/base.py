"""Loader contract: external recording -> canonical trajectories."""

from __future__ import annotations

from pathlib import Path
from typing import Protocol, runtime_checkable

from atif import Trajectory


@runtime_checkable
class TrajectoryLoader(Protocol):
    """Project one external recording format into canonical trajectories."""

    def load(self, source: str | Path) -> list[Trajectory]: ...

    def loads(self, text: str, *, source: str = "") -> list[Trajectory]: ...
