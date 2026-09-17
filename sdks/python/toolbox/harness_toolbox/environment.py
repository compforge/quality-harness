"""Accessible environments implement the common client-provider contract.

Declaring/parsing an environment does not import optional Kubernetes drivers.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import TYPE_CHECKING, ClassVar

from harness_common import ClientProvider, Environment, HostEnvironment, client_key
from harness_common.client import _ClientBorrower
from harness_common.host import parse_host

from harness_toolbox._kube_options import Options

if TYPE_CHECKING:
    from harness_toolbox.kube.client import _KubernetesAccess
    from harness_toolbox.kube.environment_resources import KubernetesResourcesClient


@dataclass(frozen=True, slots=True)
class KubernetesEnvironment(Environment, ClientProvider["KubernetesResourcesClient"]):
    """Cluster access from the optional Host; kubeconfig is a path on that host.

    Logical names identify targets, while client_key identifies reusable access.
    Options bound the shared pool and requests, not the logical environment.
    """

    kubeconfig: str = ""
    context: str | None = None
    # Deployment metadata only; it does not change Kubernetes client reuse.
    image_registry: str | None = field(default=None, kw_only=True)
    options: Options = field(
        default_factory=lambda: Options("default", 15, 4), kw_only=True, compare=False
    )
    kind: ClassVar[str] = "kubernetes"

    @property
    def client_key(self) -> str:
        return client_key(
            "kubernetes-resources",
            [
                self.kubeconfig,
                self.context,
                self.host.transport if self.host else "local",
                self.host.address if self.host else "",
                asdict(self.options),
            ],
        )

    def create_client(self, clients: _ClientBorrower) -> KubernetesResourcesClient:
        from harness_toolbox.kube.environment_resources import KubernetesResourcesClient

        return KubernetesResourcesClient(self, clients)


def _native_access(environment: KubernetesEnvironment, options: Options) -> _KubernetesAccess:
    """Internal native pool provider; remote access belongs to the Host worker."""
    from harness_toolbox.kube.client import _KubernetesAccess

    if environment.host is not None:
        environment.host.validate()
        if environment.host.transport == "ssh":
            raise ValueError(
                "Native Kubernetes access requires local cluster access; "
                "run the client on Environment.host via host.command"
            )
    return _KubernetesAccess(
        options, kubeconfig=environment.kubeconfig or None, context_name=environment.context
    )


def parse_environment(data: dict) -> Environment:
    """Parse the shared environment wire format, including legacy Kubernetes input."""
    if not isinstance(data, dict):
        raise ValueError("environment must be a mapping")
    unknown = set(data) - {
        "name",
        "kind",
        "host",
        "kubeconfig",
        "context",
        "options",
        "image_registry",
    }
    if unknown:
        raise ValueError(f"environment contains unknown field(s): {', '.join(sorted(unknown))}")
    if any(
        value is not None and not isinstance(value, str)
        for key, value in data.items()
        if key not in {"host", "options"}
    ):
        raise ValueError("environment fields except host/options must be strings")
    kind = data.get("kind") or (
        "kubernetes" if data.get("kubeconfig") or data.get("context") else "generic"
    )
    if kind not in {"generic", "host", "kubernetes"}:
        raise ValueError(f"unknown environment kind {kind!r}")
    host = parse_host(data["host"]) if data.get("host") is not None else None
    name = data.get("name") or ""
    if kind == "kubernetes":
        return KubernetesEnvironment(
            name,
            data.get("kubeconfig") or "",
            data.get("context"),
            host=host,
            image_registry=data.get("image_registry"),
            options=Options(**data["options"]) if "options" in data else Options("default", 15, 4),
        )
    if any(key in data for key in ("kubeconfig", "context", "options", "image_registry")):
        raise ValueError(f"{kind} environment contains Kubernetes access fields")
    if kind == "host":
        return HostEnvironment(name, host=host)
    return Environment(name, host=host)
