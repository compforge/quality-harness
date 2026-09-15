"""Associate logical runtime identities with independently configured data sources."""

from __future__ import annotations

from typing import TYPE_CHECKING

from harness_common.environment import KubernetesEnvironment

if TYPE_CHECKING:
    from harness_toolbox.kube import KubernetesDataSource, Options


def kubernetes_source(environment: KubernetesEnvironment, options: Options) -> KubernetesDataSource:
    """Adapt environment access facts, with explicit namespace and capacity limits.

    Logical environment names do not replace physical connection identity.
    """
    from harness_toolbox.kube import KubernetesDataSource

    if environment.host is not None:
        environment.host.validate()
        if environment.host.transport == "ssh":
            raise ValueError(
                "KubernetesDataSource requires local cluster access; run the client on "
                "Environment.host or use host.command for remote kubectl"
            )
    return KubernetesDataSource(
        options,
        kubeconfig=environment.kubeconfig or None,
        context_name=environment.context,
    )
