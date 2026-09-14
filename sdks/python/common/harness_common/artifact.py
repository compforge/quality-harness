"""A durable output produced by an ExperimentRun."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class Artifact:
    """A named domain result referenced relative to its run directory.

    The producing harness owns the content and schema. Reports render one or more Artifacts
    without becoming a new source of execution facts.
    """

    name: str
    path: str
