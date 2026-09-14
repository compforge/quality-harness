"""Deployment records and the port that applies them."""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Generic, TypeVar

from harness_common.service import Service


@dataclass(frozen=True, slots=True)
class Deployment:
    """One attempt to create or update a Service from its Component."""

    service: Service


DeploymentT = TypeVar("DeploymentT", bound=Deployment)


class Deployer(ABC, Generic[DeploymentT]):
    """Port implemented by a concrete deployment mechanism."""

    @abstractmethod
    async def deploy(self, deployment: DeploymentT) -> None:
        """Apply one Deployment and return after the Service is ready."""

    async def teardown(self) -> None:
        """Restore the baseline when the concrete deployer supports it."""
        return None
