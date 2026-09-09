"""Offline reduction from execution facts to durable Artifacts."""

from __future__ import annotations

from abc import ABC, abstractmethod
from pathlib import Path
from typing import Generic, TypeVar

from harness_common.artifact import Artifact
from harness_common.experiment import ExperimentRun


ExperimentRunT = TypeVar("ExperimentRunT", bound=ExperimentRun)


class Reducer(ABC, Generic[ExperimentRunT]):
    """Pure domain projection from an ExperimentRun to one or more Artifacts.

    Reducers read recorded execution facts and must not call the tested Service.
    Keeping reduction outside execution allows the same Outcomes to feed multiple
    Artifacts and to be reduced again offline without repeating the experiment.
    """

    @abstractmethod
    def reduce(self, run: ExperimentRunT, run_dir: Path) -> list[Artifact]:
        """Materialize derived outputs under run_dir and describe the Artifacts."""
