"""Harness-neutral experiment identity and run artifacts."""

from __future__ import annotations

from dataclasses import dataclass, field
from collections.abc import Iterator

from harness_common.artifact import Artifact
from harness_common.execution import Execution
from harness_common.operation import OperationRun
from harness_common.outcome import Outcome


@dataclass(kw_only=True)
class Experiment:
    """A named, reproducible verification intent.

    Domain harnesses add what varies and how it is executed. The shared concept owns only
    the stable name used to group runs.
    """

    name: str


@dataclass
class ExperimentRun:
    """One execution of an Experiment and the outputs it produced.

    ``experiment`` remains the Experiment name on the persisted boundary so a run artifact
    does not embed the complete, domain-specific experiment object.
    """

    run_id: str
    experiment: str
    created_at: str
    executions: list[Execution] = field(default_factory=list, kw_only=True)
    artifacts: list[Artifact] = field(default_factory=list, kw_only=True)

    def operation_runs(self) -> Iterator[OperationRun]:
        """Iterate all OperationRuns without duplicating the stored hierarchy."""
        for execution in self.executions:
            yield from execution.operation_runs

    def outcomes(self) -> Iterator[Outcome]:
        """Iterate raw Outcomes in execution order."""
        for operation_run in self.operation_runs():
            yield operation_run.outcome

    def add_artifact(self, name: str, path: str) -> None:
        """Register or replace one artifact by its logical name."""
        artifact = Artifact(name=name, path=path)
        for index, current in enumerate(self.artifacts):
            if current.name == name:
                self.artifacts[index] = artifact
                return
        self.artifacts.append(artifact)

    def artifact_paths(self) -> dict[str, str]:
        """Project typed artifacts to the cross-harness verdict wire shape."""
        return {artifact.name: artifact.path for artifact in self.artifacts}
