"""E2E reduction from recorded CaseRuns to durable artifacts."""

from __future__ import annotations

from pathlib import Path
from dataclasses import asdict
import json

from harness_common import Artifact, Reducer
from harness_common.verdict import write_verdict

from e2e_harness.engine import E2ERun
from e2e_harness.caserun import CaseRun


class E2EReducer(Reducer[E2ERun]):
    """Materialize the machine verdict without calling the tested Service."""

    def reduce(self, run: E2ERun, run_dir: Path) -> list[Artifact]:
        path = write_verdict(run_dir, run.verdict)
        artifacts = [Artifact(name="verdict", path=path.name)]
        environments = [
            {
                "case_id": case.ref.id,
                "arm_id": case.variant.id,
                "environment": asdict(case.environment),
                **(
                    {"cleanup_environment": asdict(case.cleanup_environment)}
                    if case.cleanup_environment is not None
                    else {}
                ),
            }
            for case in run.executions
            if isinstance(case, CaseRun) and case.environment is not None
        ]
        if environments:
            environments.sort(key=lambda item: (item["case_id"], item["arm_id"] or ""))
            evidence = run_dir / "environments.json"
            evidence.write_text(json.dumps(environments, indent=2) + "\n")
            artifacts.append(Artifact(name="environments", path=evidence.name))
        return artifacts
