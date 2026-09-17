"""Adapt environment declarations into independently configured client factories."""

from __future__ import annotations

from typing import TYPE_CHECKING

from harness_common.environment import KubernetesEnvironment

if TYPE_CHECKING:
    from harness_toolbox.kube import KubernetesClientFactory, Options


def kubernetes_client_factory(
    environment: KubernetesEnvironment, options: Options
) -> KubernetesClientFactory:
    """Adapt environment access facts, with explicit namespace and capacity limits.

    Logical environment names do not replace physical connection identity.
    """
    from harness_toolbox.kube import KubernetesClientFactory

    if environment.host is not None:
        environment.host.validate()
        if environment.host.transport == "ssh":
            raise ValueError(
                "KubernetesClientFactory requires local cluster access; run the client on "
                "Environment.host via host.command"
            )
    return KubernetesClientFactory(
        options,
        kubeconfig=environment.kubeconfig or None,
        context_name=environment.context,
    )
