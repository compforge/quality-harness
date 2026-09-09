"""AsyncBaseRunner — async-side protocol adapter base.

Mirror of the sync ``BaseRunner`` for async-driven callers (e.g. SSE / agent
endpoints). The two hierarchies are intentionally separate — they share the
``Request`` / ``Outcome`` data shapes but not the ``trigger`` signature
(sync vs async).

Concrete async runners: ``AsyncJSONRunner``, ``AsyncSSERunner``.
"""

from __future__ import annotations

from abc import ABC, abstractmethod

from e2e_harness.runner.base import Outcome, Request


class AsyncBaseRunner(ABC):
    """Abstract async runner — subclasses implement protocol-specific trigger."""

    @abstractmethod
    async def trigger(self, request: Request) -> Outcome:
        """Send request to the service and return standardized Outcome."""
        ...
