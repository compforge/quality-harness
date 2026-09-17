"""Execution-scoped environment access, independent of fixture orchestration."""

import time
from dataclasses import dataclass

from harness_common.client import ClientProvider
from harness_common.environment import Environment


@dataclass(frozen=True)
class EnvironmentContext:
    """Bind an environment, borrowed clients and a monotonic deadline in seconds.

    @spec Deployment, tests and diagnosis can use this context without a fixture.
    @rule It neither opens connections nor owns disposal or automatic cancellation.
    Callers propagate remaining_s into operations and join work before root exit.
    """

    environment: Environment
    clients: ClientProvider
    deadline: float

    @property
    def remaining_s(self) -> float:
        return max(0.0, self.deadline - time.monotonic())
