"""E2E reduction from recorded CaseRuns to durable artifacts."""

from __future__ import annotations

from pathlib import Path

from harness_common import Artifact, Reducer
from harness_common.verdict import write_verdict

from e2e_harness.engine import E2ERun


class E2EReducer(Reducer[E2ERun]):
    """Materialize the machine verdict without calling the tested Service."""

    def reduce(self, run: E2ERun, run_dir: Path) -> list[Artifact]:
        path = write_verdict(run_dir, run.verdict)
        return [Artifact(name="verdict", path=path.name)]
