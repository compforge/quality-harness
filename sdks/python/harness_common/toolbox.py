"""Associate logical runtime identities with independently configured data sources."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Generic, TypeVar

from harness_toolbox import Client, DataSource

from harness_common.environment import KubernetesEnvironment
from harness_common.service import Service

if TYPE_CHECKING:
    from harness_toolbox.kube import KubernetesDataSource, Options

C = TypeVar("C", bound=Client)


@dataclass(frozen=True)
class ServiceDataSource(Generic[C]):
    """One access association, not a platform resource or a client owner.

    A Service can have zero or many associations; the same source can be shared
    by multiple Services. Platform workload mappings belong to deployment config.
    Use clients.get(binding.source) so logical identity never fragments client reuse.
    """

    service: Service
    source: DataSource[C]


def kubernetes_source(
    environment: KubernetesEnvironment, options: Options
) -> KubernetesDataSource:
    """Adapt environment access facts, with explicit namespace and capacity limits.

    Logical environment names do not replace physical connection identity.
    """
    from harness_toolbox.kube import KubernetesDataSource

    return KubernetesDataSource(
        options,
        kubeconfig=environment.kubeconfig or None,
        context_name=environment.context,
    )
