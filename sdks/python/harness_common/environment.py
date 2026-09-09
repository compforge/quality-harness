"""Stable identity of one deployment environment."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class Environment:
    """A named environment in which Components have running Service instances."""

    name: str


@dataclass(frozen=True, slots=True)
class KubernetesEnvironment(Environment):
    """An Environment reachable through a Kubernetes cluster configuration."""

    kubeconfig: str = ""
    context: str | None = None
